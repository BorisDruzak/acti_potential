"""Task009 execution ACL with real instances, results, pinned files and server time."""

from datetime import UTC, datetime, timedelta

from app.identity.models import User
from app.identity.policy import Actor
from app.registry.models import RegistryAccess
from tests.test_content_security import sign_in_as
from tests.test_task007_sources_acl import SECRET_KEY, SECRET_LABEL, SECRET_OPERAND, source_process

PRIVATE_RESULT_NOTE = "SYNTHETIC-PRIVATE-RESULT-NOTE"


def execution_sample(client, factory, *, condition=False):
    setup = source_process(client, factory, condition=condition)
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["process_registry"], setup["sample"]["editor"].id)
        ).permissions = ["read", "create", "update", "download"]
    headers = sign_in_as(client, factory, setup["sample"]["editor"])
    started = client.post(
        "/api/v1/execution/instances",
        headers=headers,
        json={"process_version_id": setup["version"]["id"], "name": "Synthetic execution ACL"},
    )
    assert started.status_code == 201, started.text
    instance = started.json()
    opened = client.post(
        f"/api/v1/execution/instances/{instance['id']}/open",
        headers=headers,
        json={"revision": instance["revision"]},
    )
    assert opened.status_code == 200, opened.text
    return setup, headers, opened.json()


def action(client, headers, instance, name, *, status=200, **body):
    response = client.post(
        f"/api/v1/execution/instances/{instance['id']}/{name}",
        headers=headers,
        json={"revision": instance["revision"], **body},
    )
    assert response.status_code == status, response.text
    return response.json()


def test_reader_hidden_result_fields_do_not_leak_through_snapshot_or_event_history(client, factory):
    setup, headers, instance = execution_sample(client, factory, condition=True)
    instance = action(
        client,
        headers,
        instance,
        "result",
        values={SECRET_KEY: SECRET_OPERAND},
        comment="Synthetic confirmed answer",
        attachments=[],
    )
    editor = client.get(f"/api/v1/execution/instances/{instance['id']}")
    assert editor.status_code == 200 and SECRET_OPERAND in editor.text
    sign_in_as(client, factory, setup["sample"]["viewer"])
    detail = client.get(f"/api/v1/execution/instances/{instance['id']}?mode=viewer")
    history = client.get(f"/api/v1/execution/instances/{instance['id']}/history?mode=viewer")
    assert detail.status_code == history.status_code == 200
    for secret in (SECRET_KEY, SECRET_LABEL, SECRET_OPERAND):
        assert secret not in detail.text + history.text
    assert detail.json()["steps"][0]["result"]["values"] == {}


def test_result_attachment_and_step_basis_redact_current_closed_source(client, factory):
    setup, headers, instance = execution_sample(client, factory)
    extraction = setup["extraction"]
    instance = action(
        client,
        headers,
        instance,
        "result",
        values={},
        comment="Synthetic visible result",
        attachments=[
            {
                "document_id": setup["source"]["id"],
                "version_id": extraction["version_id"],
                "block_id": extraction["blocks"][0]["id"],
                "file_id": extraction["file_id"],
                "note": PRIVATE_RESULT_NOTE,
            }
        ],
    )
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["sample"]["registry"], setup["sample"]["viewer"].id)
        ).permissions = []
    sign_in_as(client, factory, setup["sample"]["viewer"])
    detail = client.get(f"/api/v1/execution/instances/{instance['id']}?mode=viewer")
    assert detail.status_code == 200, detail.text
    assert detail.json()["steps"][0]["result"]["attachments"][0]["restricted"] is True
    step_id = detail.json()["steps"][0]["id"]
    basis = client.get(
        f"/api/v1/execution/instances/{instance['id']}/steps/{step_id}/basis?mode=viewer"
    )
    assert basis.status_code == 200 and basis.json()["items"][0]["restricted"] is True, basis.text
    history = client.get(f"/api/v1/execution/instances/{instance['id']}/history?mode=viewer")
    for value in (
        setup["source"]["id"],
        setup["source"]["title"],
        extraction["file_id"],
        extraction["version_id"],
        extraction["blocks"][0]["id"],
        PRIVATE_RESULT_NOTE,
    ):
        assert value not in detail.text + basis.text + history.text


def test_unassigned_editor_and_admin_cannot_work_or_control_another_execution(client, factory):
    setup, _, instance = execution_sample(client, factory)
    with factory.begin() as db:
        user = User(display_name="Synthetic unassigned editor")
        db.add(user)
        db.flush()
        db.add(
            RegistryAccess(
                registry_id=setup["process_registry"],
                user_id=user.id,
                permissions=["read", "create", "update"],
            )
        )
        other = Actor(user.id, user.display_name, frozenset({"editor"}))
    for actor in (other, setup["sample"]["admin"]):
        headers = sign_in_as(client, factory, actor)
        assert client.get(f"/api/v1/execution/instances/{instance['id']}").status_code == 200
        action(
            client,
            headers,
            instance,
            "result",
            status=403,
            values={},
            comment="Forbidden result",
            attachments=[],
        )
        action(client, headers, instance, "advance", status=403)
        action(client, headers, instance, "cancel", status=403, reason="Forbidden cancellation")


def test_reader_writes_and_forged_target_are_rejected_before_server_routing(client, factory):
    setup, headers, instance = execution_sample(client, factory, condition=True)
    instance = action(
        client,
        headers,
        instance,
        "result",
        values={SECRET_KEY: SECRET_OPERAND},
        comment="Confirmed route",
        attachments=[],
    )
    action(client, headers, instance, "advance", status=422, to_step="fallback")
    viewer_headers = sign_in_as(client, factory, setup["sample"]["viewer"])
    action(
        client,
        viewer_headers,
        instance,
        "result",
        status=403,
        values={},
        comment="Forbidden reader edit",
        attachments=[],
    )
    action(client, viewer_headers, instance, "advance", status=403)
    headers = sign_in_as(client, factory, setup["sample"]["editor"])
    advanced = action(client, headers, instance, "advance")
    assert advanced["current_step_key"] == "complete"


def test_execution_scope_revocation_hides_list_counts_detail_history_and_basis(client, factory):
    setup, _, instance = execution_sample(client, factory)
    sign_in_as(client, factory, setup["sample"]["viewer"])
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["process_registry"], setup["sample"]["viewer"].id)
        ).permissions = []
    step_id = instance["steps"][0]["id"]
    for endpoint in (
        f"/execution/instances/{instance['id']}",
        f"/execution/instances/{instance['id']}/history",
        f"/execution/instances/{instance['id']}/steps/{step_id}/basis",
    ):
        response = client.get("/api/v1" + endpoint)
        assert response.status_code == 404, response.text
        assert instance["name"] not in response.text
    listed = client.get(
        "/api/v1/execution/instances", params={"registry_id": str(setup["process_registry"])}
    )
    assert (
        listed.status_code == 200 and listed.json()["total"] == 0 and listed.json()["items"] == []
    ), listed.text
    sign_in_as(client, factory, setup["sample"]["admin"])
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["process_registry"], setup["sample"]["admin"].id)
        ).permissions = []
    assert client.get(f"/api/v1/execution/instances/{instance['id']}").status_code == 404


def test_past_deadline_requires_reason_and_uses_actual_server_time(client, factory):
    setup = source_process(client, factory)
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["process_registry"], setup["sample"]["editor"].id)
        ).permissions = ["read", "create", "update"]
    headers = sign_in_as(client, factory, setup["sample"]["editor"])
    body = {
        "process_version_id": setup["version"]["id"],
        "name": "Synthetic actual-clock overdue execution",
        "deadline_at": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
    }
    denied = client.post("/api/v1/execution/instances", headers=headers, json=body)
    assert denied.status_code == 422, denied.text
    started = client.post(
        "/api/v1/execution/instances",
        headers=headers,
        json={
            **body,
            "deadline_reason": "Synthetic proof of a real past due date; server clock unchanged",
        },
    )
    assert started.status_code == 201, started.text
    opened = action(client, headers, started.json(), "open")
    assert opened["state"] == "active" and opened["overdue"] is True
    assert opened["steps"][0]["overdue"] is True


def test_completed_hidden_branch_exposes_only_generic_instance_summary(client, factory):
    setup, headers, instance = execution_sample(client, factory, condition=True)
    instance = action(
        client,
        headers,
        instance,
        "result",
        values={SECRET_KEY: SECRET_OPERAND},
        comment="Synthetic confirmed private condition",
        attachments=[],
    )
    instance = action(client, headers, instance, "advance")
    instance = action(client, headers, instance, "open")
    instance = action(
        client,
        headers,
        instance,
        "result",
        values={},
        comment="Synthetic terminal result",
        attachments=[],
    )
    instance = action(client, headers, instance, "advance")
    assert instance["state"] == "completed"
    sign_in_as(client, factory, setup["sample"]["viewer"])
    detail = client.get(f"/api/v1/execution/instances/{instance['id']}?mode=viewer")
    assert detail.status_code == 200, detail.text
    restricted = detail.json()
    assert restricted["trajectory_restricted"] is True
    assert restricted["current_step_key"] is None and restricted["assignee_id"] is None
    assert restricted["steps"] == []
    history = client.get(f"/api/v1/execution/instances/{instance['id']}/history?mode=viewer")
    assert (
        history.status_code == 200
        and history.json()["total"] == 0
        and history.json()["items"] == []
    ), history.text
    listed = client.get(
        "/api/v1/execution/instances",
        params={"registry_id": str(setup["process_registry"]), "mode": "viewer"},
    )
    assert listed.status_code == 200 and listed.json()["total"] == 1, listed.text
    row = listed.json()["items"][0]
    assert (
        row["trajectory_restricted"] is True
        and row["current_step_key"] is None
        and row["assignee_id"] is None
    )
    for serialized in (detail.text, history.text, listed.text):
        assert SECRET_KEY not in serialized and SECRET_OPERAND not in serialized
    # The static definition may retain all possible step keys; execution facts may not.
    assert restricted["state"] == "completed" and restricted["name"] == instance["name"]


def test_imported_result_document_uses_server_values_and_rechecks_source_visibility(
    client, factory
):
    setup, headers, instance = execution_sample(client, factory)
    source = client.get(f"/api/v1/documents/{setup['source']['id']}").json()
    reference = {
        "document_id": source["id"],
        "revision": source["revision"],
        "version_id": setup["extraction"]["version_id"],
    }
    instance = action(
        client,
        headers,
        instance,
        "result",
        result_document=reference,
        comment="Imported confirmed document result",
        attachments=[],
    )
    assert instance["steps"][0]["result"]["values"]["title"] == source["title"]
    action(
        client,
        headers,
        instance,
        "result",
        status=422,
        result_document=reference,
        values={"title": "Caller forged managed document value"},
        comment="Forbidden overwrite",
        attachments=[],
    )
    with factory.begin() as db:
        db.get(
            RegistryAccess, (setup["sample"]["registry"], setup["sample"]["viewer"].id)
        ).permissions = []
    sign_in_as(client, factory, setup["sample"]["viewer"])
    detail = client.get(f"/api/v1/execution/instances/{instance['id']}?mode=viewer")
    assert detail.status_code == 200, detail.text
    assert detail.json()["steps"][0]["result"]["values"] == {}
    assert source["id"] not in detail.text and source["title"] not in detail.text
    result_list = client.get(
        f"/api/v1/execution/instances/{instance['id']}/steps/{instance['steps'][0]['id']}/results?mode=viewer"
    )
    assert result_list.status_code == 200, result_list.text
    assert source["id"] not in result_list.text and source["title"] not in result_list.text
