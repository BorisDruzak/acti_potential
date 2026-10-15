import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.registry.models import RegistryAccess
from tests.test_content_security import sign_in_as
from tests.test_task005_content import synthetic_docx
from tests.test_task007_sources_acl import source_process


def route_sample(client, factory, *, private=False, file_form=False):
    setup = source_process(client, factory)
    headers = sign_in_as(client, factory, setup["sample"]["admin"])
    fields = [
        {
            "key": "choice",
            "label": "Choice",
            "type": "integer",
            "required": True,
            "constraints": {"min": 1, "max": 3},
        },
        {
            "key": "details",
            "label": "Details for choice one",
            "type": "text",
            "visible_when": {"field": "choice", "op": "eq", "value": 1},
            "required_when": {"field": "choice", "op": "eq", "value": 1},
        },
    ]
    if private:
        fields[0]["read_roles"] = ["editor"]
    if file_form:
        fields = [
            {"key": "title", "label": "Title", "type": "text", "system": "title", "required": True},
            {
                "key": "reg_number",
                "label": "Managed readonly number",
                "type": "text",
                "system": "reg_number",
                "editable": False,
                "default": "SYNTHETIC-AUTO",
                "required": True,
            },
            {"key": "original", "label": "Required original", "type": "file", "required": True},
        ]
    schema = client.post(
        f"/api/v1/document-types/{setup['source']['document_type_id']}/versions",
        headers=headers,
        json={"schema": {"sections": [{"key": "result", "title": "Result", "fields": fields}]}},
    )
    assert schema.status_code == 201, schema.text
    schema_id = schema.json()["id"]
    assert (
        client.post(
            f"/api/v1/form-schemas/{schema_id}/publish", headers=headers, json={}
        ).status_code
        == 200
    )
    draft = client.post(
        f"/api/v1/processes/{setup['process']['id']}/versions", headers=headers, json={}
    ).json()
    steps = [
        {
            "key": "start",
            "title": "Synthetic start",
            "is_start": True,
            "form_schema_id": schema_id,
            "inputs": ["Required input"],
            "outputs": ["Required output"],
            "control_note": "Required control",
        },
        {"key": "first", "title": "First route"},
        {"key": "second", "title": "Second route"},
        {"key": "fallback", "title": "Fallback route"},
        {"key": "finish", "title": "Finish", "is_terminal": True},
    ]
    transitions = (
        [{"key": "initial", "from_step": "start", "to_step": "first"}]
        if file_form
        else [
            {
                "key": "choose_first",
                "from_step": "start",
                "to_step": "first",
                "condition": {"field": "choice", "op": "eq", "value": 1},
            },
            {
                "key": "choose_second",
                "from_step": "start",
                "to_step": "second",
                "condition": {"field": "choice", "op": "eq", "value": 2},
            },
            {"key": "otherwise", "from_step": "start", "to_step": "fallback", "is_else": True},
        ]
    )
    if file_form:
        steps = [steps[0], steps[1], steps[-1]]
        transitions.append({"key": "first_finish", "from_step": "first", "to_step": "finish"})
    else:
        transitions.extend(
            {"key": key + "_finish", "from_step": key, "to_step": "finish"}
            for key in ["first", "second", "fallback"]
        )
    saved = client.put(
        f"/api/v1/process-versions/{draft['id']}",
        headers=headers,
        json={
            "revision": draft["revision"],
            "effective_from": "2026-01-01",
            "steps": steps,
            "transitions": transitions,
        },
    )
    assert saved.status_code == 200, saved.text
    published = client.post(
        f"/api/v1/process-versions/{draft['id']}/publish",
        headers=headers,
        json={"revision": saved.json()["revision"]},
    )
    assert published.status_code == 200, published.text
    setup["version"], setup["schema_id"] = published.json(), schema_id
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["process_registry"], setup["sample"]["editor"].id)
        ).permissions = ["read", "create", "update", "upload", "download"]
    headers = sign_in_as(client, factory, setup["sample"]["editor"])
    started = client.post(
        "/api/v1/execution/instances",
        headers=headers,
        json={
            "process_version_id": setup["version"]["id"],
            "name": "Synthetic controlled execution",
        },
    )
    assert started.status_code == 201, started.text
    return setup, headers, started.json()


def action(client, headers, instance, name, expected=200, **data):
    response = client.post(
        f"/api/v1/execution/instances/{instance['id']}/{name}",
        headers=headers,
        json={"revision": instance["revision"], **data},
    )
    assert response.status_code == expected, response.text
    return response.json()


def current(instance):
    return next(
        step for step in instance["steps"] if step["id"] == instance["current_step_execution_id"]
    )


def complete_checks(client, headers, instance):
    return action(
        client,
        headers,
        instance,
        "checklist",
        items=[{"id": item["id"], "checked": True} for item in current(instance)["checklist"]],
    )


@pytest.mark.parametrize("choice,target", [(1, "first"), (2, "second"), (3, "fallback")])
def test_server_routes_confirmed_typed_values_after_required_form_and_checklist(
    client, factory, choice, target
):
    _, headers, instance = route_sample(client, factory)
    snapshot_fields = instance["process_snapshot"]["steps"][0]["form_schema"]["sections"][0][
        "fields"
    ]
    assert (
        next(field for field in snapshot_fields if field["key"] == "details")["visible_when"][
            "value"
        ]
        == 1
    )
    instance = action(client, headers, instance, "open")
    for values in ({}, {"choice": "1"}, {"choice": 4}, {"choice": 1}):
        action(client, headers, instance, "result", expected=422, values=values)
    values = {
        "choice": choice,
        **({"details": "Required explanatory answer"} if choice == 1 else {}),
    }
    instance = action(client, headers, instance, "result", values=values)
    action(client, headers, instance, "advance", expected=422)
    instance = complete_checks(client, headers, instance)
    instance = action(client, headers, instance, "advance")
    assert instance["current_step_key"] == target and current(instance)["state"] == "pending"


def test_hidden_controller_removes_dependent_field_schema_and_blocks_work_without_guessing(
    client, factory
):
    setup, headers, instance = route_sample(client, factory, private=True)
    instance = action(client, headers, instance, "open")
    sign_in_as(client, factory, setup["sample"]["viewer"])
    displayed = client.get(f"/api/v1/execution/instances/{instance['id']}?mode=viewer")
    assert displayed.status_code == 200, displayed.text
    schema = displayed.json()["process_snapshot"]["steps"][0]["form_schema"]
    assert schema["sections"][0]["fields"] == []
    assert '"field":"choice"' not in displayed.text and '"key":"details"' not in displayed.text


def test_return_creates_new_attempt_and_cannot_rewrite_immutable_completion_results_or_snapshot(
    client, factory
):
    setup, headers, instance = route_sample(client, factory)
    instance = action(client, headers, instance, "open")
    instance = action(
        client, headers, instance, "result", values={"choice": 2}, comment="Frozen first attempt"
    )
    result_id, first_id = current(instance)["result"]["id"], current(instance)["id"]
    instance = complete_checks(client, headers, instance)
    instance = action(client, headers, instance, "advance")
    instance = action(client, headers, instance, "return", reason="Synthetic correction")
    assert current(instance)["step_key"] == "start" and current(instance)["attempt"] == 2
    old = next(step for step in instance["steps"] if step["id"] == first_id)
    assert old["state"] == "completed" and old["result"]["comment"] == "Frozen first attempt"
    actor = str(setup["sample"]["editor"].id)
    for sql, values in [
        ("UPDATE step_results SET comment='rewritten' WHERE id=:id", {"id": UUID(result_id)}),
        ("UPDATE execution_instances SET snapshot='{}' WHERE id=:id", {"id": UUID(instance["id"])}),
        ("UPDATE step_executions SET state='active' WHERE id=:id", {"id": UUID(first_id)}),
        (
            "INSERT INTO execution_checklist(id,step_execution_id,ordinal,label,kind,required,checked) VALUES(:id,:step,99,'forged','control',true,false)",
            {"id": uuid4(), "step": UUID(first_id)},
        ),
        (
            "INSERT INTO step_results(id,step_execution_id,values,comment,attachments,actor_id) VALUES(:id,:step,'{}','forged','[]',:actor)",
            {"id": uuid4(), "step": UUID(first_id), "actor": UUID(actor)},
        ),
    ]:
        with pytest.raises(Exception, match="immutable"):
            with factory.begin() as db:
                db.execute(text(sql), values)


def test_suspension_cancellation_and_concurrent_revision_are_serialized(client, factory):
    _, headers, instance = route_sample(client, factory)
    endpoint = f"/api/v1/execution/instances/{instance['id']}/open"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                lambda _: client.post(
                    endpoint, headers=headers, json={"revision": instance["revision"]}
                ),
                range(2),
            )
        )
    assert sorted(row.status_code for row in outcomes) == [200, 409]
    instance = next(row.json() for row in outcomes if row.status_code == 200)
    instance = action(client, headers, instance, "suspend", reason="Synthetic paused")
    action(client, headers, instance, "result", expected=409, values={"choice": 2})
    instance = action(client, headers, instance, "resume", reason="Synthetic resumed")
    instance = action(client, headers, instance, "cancel", reason="Synthetic cancellation")
    assert instance["state"] == "cancelled"
    action(client, headers, instance, "resume", expected=409, reason="No terminal rewrite")
    for sql, parameters in [
        (
            "INSERT INTO step_executions(id,instance_id,step_key,attempt,state,assignee_id) VALUES(:id,:instance,'forged',1,'pending',:actor)",
            {
                "id": uuid4(),
                "instance": UUID(instance["id"]),
                "actor": UUID(instance["assignee_id"]),
            },
        ),
        (
            "UPDATE step_executions SET state='pending' WHERE id=:id",
            {"id": UUID(instance["current_step_execution_id"])},
        ),
    ]:
        with pytest.raises(Exception, match="immutable"):
            with factory.begin() as db:
                db.execute(text(sql), parameters)


def test_readonly_managed_and_file_values_are_imported_from_actual_metadata_revision(
    client, factory
):
    setup, headers, instance = route_sample(client, factory, file_form=True)
    instance = action(client, headers, instance, "open")
    action(
        client,
        headers,
        instance,
        "result",
        expected=422,
        values={"title": "Inline missing original"},
    )
    original = synthetic_docx("Synthetic execution result original")
    created = client.post(
        "/api/v1/documents",
        headers=headers,
        json={
            "registry_id": str(setup["process_registry"]),
            "document_type_id": setup["source"]["document_type_id"],
            "schema_version_id": setup["schema_id"],
            "values": {"title": "Actual result card"},
            "files": [
                {
                    "field_key": "original",
                    "name": "result.docx",
                    "content_base64": base64.b64encode(original).decode(),
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    document = created.json()
    instance = action(
        client,
        headers,
        instance,
        "result",
        result_document={"document_id": document["id"], "revision": document["revision"]},
    )
    result = current(instance)["result"]
    assert result["values"]["reg_number"] == "SYNTHETIC-AUTO" and result["values"]["original"] == [
        document["files"][0]["id"]
    ]
    assert (
        result["result_document"]["metadata_revision_id"]
        and result["result_document"]["schema_version_id"] == setup["schema_id"]
    )
    changed = client.put(
        f"/api/v1/documents/{document['id']}",
        headers=headers,
        json={"revision": document["revision"], "values": {"title": "Changed after confirmation"}},
    )
    assert changed.status_code == 200, changed.text
    instance = complete_checks(client, headers, instance)
    action(client, headers, instance, "advance", expected=409)


def test_overdue_uses_injectable_domain_clock_without_a_production_time_override(
    client, factory, monkeypatch
):
    from app.execution import service

    _, headers, instance = route_sample(client, factory)
    future = datetime.now(UTC) + timedelta(days=2)
    instance = action(
        client,
        headers,
        instance,
        "deadline",
        deadline_at=future.isoformat(),
        reason="Synthetic expected completion",
    )
    assert not instance["overdue"]
    monkeypatch.setattr(service, "now", lambda: future + timedelta(seconds=1))
    displayed = client.get(f"/api/v1/execution/instances/{instance['id']}").json()
    assert displayed["overdue"] is True
    history = client.get(f"/api/v1/execution/instances/{instance['id']}/history").json()["items"]
    assert history[-1]["action"] == "deadline.changed"


def test_execution_requires_a_real_managed_number_even_though_unregistered_cards_allow_blank(
    factory,
):
    from fastapi import HTTPException

    from app.execution.service import required_access
    from app.registry.value_validation import validate_values
    from tests.test_content_security import actors_and_document

    sample = actors_and_document(factory)
    field = {
        "key": "number",
        "label": "Number",
        "type": "text",
        "system": "reg_number",
        "number_series_id": str(uuid4()),
        "required": True,
    }
    schema = {"sections": [{"fields": [field]}]}
    with factory.begin() as db:
        assert validate_values(db, sample["editor"], schema, {}, sample["registry"]) == {}
    with pytest.raises(HTTPException) as error:
        required_access(sample["editor"], {"number": field}, {})
    assert error.value.status_code == 422


def test_unversioned_published_basis_is_frozen_to_exact_permitted_file_edition_at_start(
    client, factory
):
    from app.processes import service as processes
    from app.processes.models import ProcessVersion

    setup, headers, _ = route_sample(client, factory)
    created = client.post(
        "/api/v1/documents",
        headers=headers,
        json={
            "registry_id": str(setup["process_registry"]),
            "document_type_id": setup["source"]["document_type_id"],
            "schema_version_id": setup["source"]["schema_version_id"],
            "values": {"title": "Initially metadata only basis"},
        },
    )
    assert created.status_code == 201, created.text
    document = created.json()
    admin_headers = sign_in_as(client, factory, setup["sample"]["admin"])
    draft = client.post(
        f"/api/v1/processes/{setup['process']['id']}/versions", headers=admin_headers, json={}
    ).json()
    with factory.begin() as db:
        steps, transitions = processes.graph(db, db.get(ProcessVersion, UUID(draft["id"])))
    steps[0]["sources"] = [
        {"document_id": document["id"], "note": "Published before an original existed"}
    ]
    for edge in transitions:
        edge.pop("ordinal", None)
    saved = client.put(
        f"/api/v1/process-versions/{draft['id']}",
        headers=admin_headers,
        json={
            "revision": draft["revision"],
            "effective_from": "2026-01-01",
            "steps": steps,
            "transitions": transitions,
        },
    )
    assert saved.status_code == 200, saved.text
    published = client.post(
        f"/api/v1/process-versions/{draft['id']}/publish",
        headers=admin_headers,
        json={"revision": saved.json()["revision"]},
    )
    assert published.status_code == 200, published.text
    headers = sign_in_as(client, factory, setup["sample"]["editor"])
    first_bytes = synthetic_docx("Basis original created before execution")
    first = client.put(
        f"/api/v1/documents/{document['id']}",
        headers=headers,
        json={
            "revision": document["revision"],
            "values": {},
            "files": [
                {
                    "field_key": "public_file",
                    "name": "basis-v1.docx",
                    "content_base64": base64.b64encode(first_bytes).decode(),
                }
            ],
        },
    )
    assert first.status_code == 200, first.text
    started = client.post(
        "/api/v1/execution/instances",
        headers=headers,
        json={"process_version_id": draft["id"], "name": "Execution freezes a late original"},
    )
    assert started.status_code == 201, started.text
    instance = started.json()
    frozen_version = instance["process_snapshot"]["steps"][0]["sources"][0]["version_id"]
    options = client.get(f"/api/v1/documents/{document['id']}/relation-options").json()
    assert frozen_version == options["versions"][0]["id"]
    second = client.put(
        f"/api/v1/documents/{document['id']}",
        headers=headers,
        json={
            "revision": first.json()["revision"],
            "values": {},
            "files": [
                {
                    "field_key": "public_file",
                    "name": "basis-v2.docx",
                    "content_base64": base64.b64encode(
                        synthetic_docx("Changed source after start")
                    ).decode(),
                }
            ],
        },
    )
    assert second.status_code == 200, second.text
    basis = client.get(
        f"/api/v1/execution/instances/{instance['id']}/steps/{instance['current_step_execution_id']}/basis"
    )
    assert basis.status_code == 200, basis.text
    assert basis.json()["items"][0]["anchor"]["version_id"] == frozen_version
    oldfile = basis.json()["items"][0]["files"][0]["id"]
    assert client.get(f"/api/v1/files/{oldfile}/download").content == first_bytes
