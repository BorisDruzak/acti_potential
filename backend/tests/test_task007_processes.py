from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text

from app.registry.models import RegistryAccess
from tests.test_content_security import actors_and_document, sign_in_as


def setup_process(client, factory):
    sample = actors_and_document(factory)
    with factory.begin() as db:
        db.add(
            RegistryAccess(
                registry_id=sample["registry"],
                user_id=sample["admin"].id,
                permissions=["read", "update"],
            )
        )
    headers = sign_in_as(client, factory, sample["admin"])
    response = client.post(
        "/api/v1/processes",
        headers=headers,
        json={
            "registry_id": str(sample["registry"]),
            "code": "synthetic-process",
            "name": "Original process",
            "purpose": "Original purpose",
        },
    )
    assert response.status_code == 201, response.text
    process = response.json()
    version = client.get(f"/api/v1/processes/{process['id']}/versions").json()[0]
    return sample, headers, process, version


def valid_graph(version):
    return {
        "revision": version["revision"],
        "effective_from": date.today().isoformat(),
        "steps": [
            {"key": "start", "title": "Start", "is_start": True},
            {"key": "finish", "title": "Finish", "is_terminal": True},
        ],
        "transitions": [{"key": "continue", "from_step": "start", "to_step": "finish"}],
    }


@pytest.mark.parametrize(
    "issue",
    [
        "missing_start",
        "unreachable",
        "cycle",
        "dangling",
        "terminal_outgoing",
        "duplicate",
        "no_else",
    ],
)
def test_publish_validates_entire_graph_but_keeps_draft_editable(client, factory, issue):
    _, headers, _, version = setup_process(client, factory)
    body = valid_graph(version)
    if issue == "missing_start":
        body["steps"][0]["is_start"] = False
    elif issue == "unreachable":
        body["steps"].append({"key": "orphan", "title": "Orphan", "is_terminal": True})
    elif issue == "cycle":
        body["transitions"][0]["to_step"] = "start"
    elif issue == "dangling":
        body["transitions"][0]["to_step"] = "missing"
    elif issue == "terminal_outgoing":
        body["transitions"].append({"key": "reverse", "from_step": "finish", "to_step": "start"})
    elif issue == "duplicate":
        body["transitions"].append({**body["transitions"][0], "key": "duplicate"})
    else:
        body["transitions"][0]["is_else"] = True
    saved = client.put(f"/api/v1/process-versions/{version['id']}", headers=headers, json=body)
    assert saved.status_code == 200, saved.text
    for endpoint in ("validate", "publish"):
        response = client.post(
            f"/api/v1/process-versions/{version['id']}/{endpoint}",
            headers=headers,
            json={"revision": saved.json()["revision"]},
        )
        assert response.status_code == 422, response.text
    assert client.get(f"/api/v1/process-versions/{version['id']}").json()["state"] == "draft"


def test_timeline_metadata_copy_revision_conflict_and_db_immutability(client, factory):
    sample, headers, process, draft = setup_process(client, factory)
    body = valid_graph(draft)
    body["effective_from"] = (date.today() - timedelta(days=10)).isoformat()
    saved = client.put(f"/api/v1/process-versions/{draft['id']}", headers=headers, json=body).json()
    conflict = client.put(f"/api/v1/process-versions/{draft['id']}", headers=headers, json=body)
    assert conflict.status_code == 409
    assert client.post(
        f"/api/v1/process-versions/{draft['id']}/validate",
        headers=headers,
        json={"revision": saved["revision"]},
    ).json() == {"valid": True}
    published = client.post(
        f"/api/v1/process-versions/{draft['id']}/publish",
        headers=headers,
        json={"revision": saved["revision"]},
    )
    assert published.status_code == 200, published.text
    frozen = published.json()
    assert (
        client.patch(
            f"/api/v1/processes/{process['id']}",
            headers=headers,
            json={"name": "New process", "purpose": "New purpose"},
        ).status_code
        == 200
    )
    second = client.post(
        f"/api/v1/processes/{process['id']}/versions", headers=headers, json={}
    ).json()
    body = valid_graph(second)
    body["effective_from"] = (date.today() + timedelta(days=5)).isoformat()
    saved = client.put(
        f"/api/v1/process-versions/{second['id']}", headers=headers, json=body
    ).json()
    assert (
        client.post(
            f"/api/v1/process-versions/{second['id']}/publish",
            headers=headers,
            json={"revision": saved["revision"]},
        ).status_code
        == 200
    )
    assert client.get(f"/api/v1/processes/{process['id']}").json()["name"] == "Original process"
    assert (
        client.get(f"/api/v1/processes/{process['id']}?as_of={body['effective_from']}").json()[
            "version"
        ]["id"]
        == second["id"]
    )
    assert client.get(f"/api/v1/process-versions/{draft['id']}").json() == frozen
    assert (
        client.put(
            f"/api/v1/process-versions/{draft['id']}",
            headers=headers,
            json={**body, "revision": frozen["revision"]},
        ).status_code
        == 409
    )
    for sql in [
        "UPDATE process_versions SET metadata_snapshot='{}' WHERE id=:id",
        "UPDATE process_steps SET data='{}' WHERE version_id=:id",
        "DELETE FROM process_transitions WHERE version_id=:id",
    ]:
        with pytest.raises(Exception, match="immutable"):
            with factory.begin() as db:
                db.execute(text(sql), {"id": UUID(draft["id"])})
    copied = client.post(
        f"/api/v1/processes/{process['id']}/copy",
        headers=headers,
        json={"registry_id": str(sample["registry"]), "code": "synthetic-copy", "name": "Copy"},
    )
    assert copied.status_code == 201, copied.text
    assert (
        client.get(f"/api/v1/processes/{copied.json()['id']}/versions").json()[0]["state"]
        == "draft"
    )


def test_draft_and_future_configuration_are_not_a_reader_metadata_oracle(client, factory):
    sample, _, process, draft = setup_process(client, factory)
    sign_in_as(client, factory, sample["viewer"])
    assert client.get("/api/v1/processes").json() == []
    assert client.get(f"/api/v1/processes/{process['id']}").status_code == 404
    assert client.get(f"/api/v1/process-versions/{draft['id']}").status_code in {403, 404}


def test_condition_validation_reuses_canonical_typed_six_operators():
    from fastapi import HTTPException

    from app.processes.service import condition_valid

    fields = {"amount": {"key": "amount", "label": "Amount", "type": "integer"}}
    for op in ["eq", "ne", "in", "not_in", "present", "absent"]:
        condition = {"field": "amount", "op": op}
        if op in {"eq", "ne"}:
            condition["value"] = 5
        elif op in {"in", "not_in"}:
            condition["value"] = [1, 5]
        condition_valid(condition, fields)
        broken = deepcopy(condition)
        broken["value"] = "unsafe"
        with pytest.raises(HTTPException):
            condition_valid(broken, fields)


def test_concurrent_draft_update_has_one_winner_and_no_partial_child_graph(client, factory):
    _, headers, _, draft = setup_process(client, factory)
    first = valid_graph(draft)
    second = deepcopy(first)
    second["steps"][0]["title"] = "Concurrent second"
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda body: client.put(
                    f"/api/v1/process-versions/{draft['id']}", headers=headers, json=body
                ),
                [first, second],
            )
        )
    assert sorted(response.status_code for response in responses) == [200, 409]
    saved = client.get(f"/api/v1/process-versions/{draft['id']}").json()
    assert saved["revision"] == draft["revision"] + 1
    assert len(saved["steps"]) == 2 and len(saved["transitions"]) == 1


def test_required_condition_preview_is_typed_ordered_and_missing_values_cannot_choose_else(
    client, factory
):
    from sqlalchemy import select

    from app.registry.models import FormSchema, Registry

    _, headers, process, draft = setup_process(client, factory)
    with factory.begin() as db:
        schema = db.scalar(select(FormSchema))
        form = FormSchema(
            document_type_id=schema.document_type_id,
            version=2,
            state="published",
            schema_data={
                "sections": [
                    {
                        "key": "routing",
                        "title": "Routing",
                        "fields": [
                            {
                                "key": "amount",
                                "label": "Amount",
                                "type": "integer",
                                "required": True,
                            }
                        ],
                    }
                ]
            },
        )
        db.add(form)
        db.get(Registry, UUID(process["registry_id"])).document_type_ids = [
            str(schema.document_type_id)
        ]
        db.flush()
        form_id = str(form.id)
    body = valid_graph(draft)
    body["steps"][0]["form_schema_id"] = form_id
    body["steps"].extend(
        [
            {"key": "second", "title": "Second", "is_terminal": True},
            {"key": "fallback", "title": "Fallback", "is_terminal": True},
        ]
    )
    body["transitions"] = [
        {
            "key": "first_match",
            "from_step": "start",
            "to_step": "finish",
            "condition": {"field": "amount", "op": "in", "value": [1, 5]},
        },
        {
            "key": "second_match",
            "from_step": "start",
            "to_step": "second",
            "condition": {"field": "amount", "op": "eq", "value": 5},
        },
        {"key": "else", "from_step": "start", "to_step": "fallback", "is_else": True},
    ]
    saved = client.put(f"/api/v1/process-versions/{draft['id']}", headers=headers, json=body)
    assert saved.status_code == 200, saved.text
    endpoint = f"/api/v1/process-versions/{draft['id']}/preview"
    for values in ({}, {"amount": "5"}):
        assert (
            client.post(
                endpoint, headers=headers, json={"step_key": "start", "values": values}
            ).status_code
            == 422
        )
    assert (
        client.post(
            endpoint, headers=headers, json={"step_key": "start", "values": {"amount": 5}}
        ).json()["transition_key"]
        == "first_match"
    )
    assert (
        client.post(
            endpoint, headers=headers, json={"step_key": "start", "values": {"amount": 7}}
        ).json()["transition_key"]
        == "else"
    )


@pytest.mark.parametrize(
    "condition",
    [
        {},
        {"field": []},
        {"field": "unknown", "op": "eq", "value": "x"},
        {"field": "unknown", "op": "eval", "value": "x"},
    ],
)
@pytest.mark.parametrize("is_else", [False, True])
def test_invalid_single_edge_condition_draft_does_not_crash_projection_or_publish(
    client, factory, condition, is_else
):
    _, headers, _, draft = setup_process(client, factory)
    body = valid_graph(draft)
    body["transitions"][0]["condition"] = condition
    body["transitions"][0]["is_else"] = is_else
    saved = client.put(f"/api/v1/process-versions/{draft['id']}", headers=headers, json=body)
    assert saved.status_code == 200, saved.text
    validated = client.post(
        f"/api/v1/process-versions/{draft['id']}/validate",
        headers=headers,
        json={"revision": saved.json()["revision"]},
    )
    assert validated.status_code == 422, validated.text
    assert validated.json()["error"]["code"] == "process_validation"
    assert (
        client.post(
            f"/api/v1/process-versions/{draft['id']}/publish",
            headers=headers,
            json={"revision": saved.json()["revision"]},
        ).status_code
        == 422
    )
