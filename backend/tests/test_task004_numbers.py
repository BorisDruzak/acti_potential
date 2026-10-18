from datetime import UTC, datetime
from types import SimpleNamespace

import pytest


def test_period_boundary_timezone_and_manual_override():
    from app.numbering.service import period_key

    series = SimpleNamespace(
        current_period=None,
        period_mode="annual",
        timezone="Asia/Yekaterinburg",
        boundary_month=1,
        boundary_day=1,
    )
    assert period_key(series, datetime(2026, 12, 31, 20, tzinfo=UTC)) == "2027"
    series.boundary_month = 4
    assert period_key(series, datetime(2026, 3, 31, 18, tzinfo=UTC)) == "2025"
    assert period_key(series, datetime(2026, 3, 31, 20, tzinfo=UTC)) == "2026"
    series.current_period = "pilot-next"
    assert period_key(series) == "pilot-next"


def test_number_format_is_declarative_and_length_bounded():
    from uuid import uuid4

    from app.numbering.contracts import SeriesInput
    from app.numbering.service import render_number

    data = SeriesInput(
        code="pilot",
        name="Pilot",
        registry_id=uuid4(),
        prefix="P/",
        padding=5,
        format="{prefix}{period}/{ordinal}{suffix}",
    )
    assert render_number(data, "2026", 7) == "P/2026/00007"
    for mask in ("{__class__}", "{ordinal.__class__}", "fixed", "{ordinal!r}", "{ordinal:999999}"):
        with pytest.raises(ValueError):
            SeriesInput(code="pilot", name="Pilot", registry_id=uuid4(), format=mask)


def setup_series(client, factory, **changes):
    from tests.conftest import login_admin
    from tests.test_config_api import admin_headers

    login_admin(client, factory)
    headers = admin_headers(client)
    kind = client.post(
        "/api/v1/document-types",
        headers=headers,
        json={"code": "number-pilot", "name": "Number pilot"},
    ).json()
    registry = client.post(
        "/api/v1/registries",
        headers=headers,
        json={"code": "number-pilot", "name": "Number pilot", "document_type_ids": [kind["id"]]},
    ).json()
    me = client.get("/api/v1/users/me").json()
    assert (
        client.put(
            f"/api/v1/admin/access/{registry['id']}",
            headers=headers,
            json={"entries": [{"user_id": me["id"], "permissions": ["read", "create", "update"]}]},
        ).status_code
        == 200
    )
    created = client.post(
        "/api/v1/number-series",
        headers=headers,
        json={
            "code": "pilot",
            "name": "Pilot",
            "registry_id": registry["id"],
            "document_type_ids": [kind["id"]],
            "current_period": "2026",
            "prefix": "P/",
            **changes,
        },
    )
    assert created.status_code == 201, created.text
    series = created.json()
    granted = client.put(
        f"/api/v1/number-series/{series['id']}/grants",
        headers=headers,
        json={
            "entries": [
                {
                    "user_id": me["id"],
                    "permissions": ["view", "reserve", "assign", "cancel", "void"],
                }
            ]
        },
    )
    assert granted.status_code == 200, granted.text
    return series, registry, kind, me, headers


def test_reserve_retry_cancel_nonreuse_and_period_transition(client, factory):
    series, registry, kind, me, headers = setup_series(client, factory, max_batch=3)
    endpoint = f"/api/v1/number-series/{series['id']}"
    data = {"count": 2, "idempotency_key": "synthetic-reserve-001"}
    reserved = client.post(endpoint + "/reserve", headers=headers, json=data)
    assert reserved.status_code == 200, reserved.text
    entries = reserved.json()["items"]
    assert [e["number"] for e in entries] == ["P/0001", "P/0002"]
    assert client.post(endpoint + "/reserve", headers=headers, json=data).json() == reserved.json()
    assert (
        client.post(endpoint + "/reserve", headers=headers, json={**data, "count": 3}).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/number-entries/{entries[0]['id']}/cancel",
            headers=headers,
            json={"reason": "Synthetic cancellation", "idempotency_key": "cancel-one"},
        ).status_code
        == 200
    )
    next_entry = client.post(
        endpoint + "/reserve", headers=headers, json={"idempotency_key": "next-number"}
    ).json()["items"][0]
    assert next_entry["ordinal"] == 3
    assert (
        client.patch(endpoint, headers=headers, json={"current_period": "2027"}).status_code == 200
    )
    next_period = client.post(
        endpoint + "/reserve", headers=headers, json={"idempotency_key": "new-period"}
    ).json()["items"][0]
    # A new period keeps its counter, but the rendered registration number
    # cannot collide with previous periods or spent reservations.
    assert next_period["ordinal"] == 4 and next_period["period_key"] == "2027"
    assert (
        client.post(
            endpoint + "/reserve", headers=headers, json={"count": 4, "idempotency_key": "too-many"}
        ).status_code
        == 422
    )
    assert client.get(endpoint + "/stats").json()["next_ordinal"] == 5


def bound_document(
    client,
    series,
    registry,
    kind,
    headers,
    constraints=None,
    required_condition=False,
    title_roles=None,
):
    draft = client.get(f"/api/v1/document-types/{kind['id']}/versions").json()[0]
    fields = [
        {
            "key": "number",
            "label": "Number",
            "type": "text",
            "system": "reg_number",
            "number_series_id": series["id"],
            "required": True,
            "constraints": constraints or {},
        },
        {"key": "title", "label": "Title", "type": "text", "system": "title"},
    ]
    if title_roles:
        fields[1]["read_roles"] = title_roles
    if required_condition:
        fields.append(
            {
                "key": "justification",
                "label": "Justification",
                "type": "text",
                "required_when": {"field": "number", "op": "present"},
            }
        )
    schema = {"sections": [{"key": "main", "title": "Main", "fields": fields}]}
    assert (
        client.put(
            f"/api/v1/form-schemas/{draft['id']}", headers=headers, json={"schema": schema}
        ).status_code
        == 200
    )
    published = client.post(f"/api/v1/form-schemas/{draft['id']}/publish", headers=headers, json={})
    assert published.status_code == 200, published.text
    # Explicit assignment still supports existing, unnumbered official cards.
    # New API-created bound cards now reserve a number during creation; build
    # this pre-existing state directly rather than disabling that lifecycle.
    from uuid import UUID

    from app.registry.documents import append_history
    from app.registry.models import Document

    me = client.get("/api/v1/users/me").json()
    with client.app.state.factory.begin() as db:
        document = Document(
            registry_id=UUID(registry["id"]),
            document_type_id=UUID(kind["id"]),
            schema_version_id=UUID(draft["id"]),
            title="Synthetic numbered document",
            created_by=UUID(me["id"]),
            card_state="published",
            publication_targets=["internal", "shared"],
        )
        db.add(document)
        db.flush()
        append_history(db, document, schema, SimpleNamespace(id=UUID(me["id"])))
        identity = document.id
    return client.get(f"/api/v1/documents/{identity}").json()


def test_assignment_revision_history_void_and_manual_guard(client, factory):
    from uuid import UUID

    from sqlalchemy import select

    from app.numbering.models import NumberOperation

    series, registry, kind, me, headers = setup_series(client, factory)
    card = bound_document(client, series, registry, kind, headers)
    entry = client.post(
        f"/api/v1/number-series/{series['id']}/reserve",
        headers=headers,
        json={"idempotency_key": "reserve-one"},
    ).json()["items"][0]
    endpoint = f"/api/v1/number-entries/{entry['id']}"
    payload = {"document_id": card["id"], "revision": 100, "idempotency_key": "assignment"}
    assert client.post(endpoint + "/assign", headers=headers, json=payload).status_code == 409
    payload["revision"] = card["revision"]
    assigned = client.post(endpoint + "/assign", headers=headers, json=payload)
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["state"] == "assigned"
    assert (
        client.post(endpoint + "/assign", headers=headers, json=payload).json() == assigned.json()
    )
    card_url = f"/api/v1/documents/{card['id']}"
    updated = client.get(card_url).json()
    assert updated["reg_number"] == entry["number"] and updated["revision"] == 2
    assert (
        client.put(
            card_url, headers=headers, json={"revision": 2, "values": {"number": "hack"}}
        ).status_code
        == 403
    )
    assert (
        client.post(
            endpoint + "/void",
            headers=headers,
            json={"reason": "Synthetic void", "idempotency_key": "void-one"},
        ).status_code
        == 200
    )
    assert client.get(card_url).json()["reg_number"] == entry["number"]
    with factory() as db:
        assert [
            row.action
            for row in db.scalars(
                select(NumberOperation)
                .where(NumberOperation.entry_id == UUID(entry["id"]))
                .order_by(NumberOperation.created_at)
            )
        ] == ["reserved", "assigned", "void"]


@pytest.mark.parametrize(
    "constraints,conditional", [({"pattern": "^[0-9]{4}$"}, False), ({}, True)]
)
def test_assignment_obeys_form_constraints_and_new_required_conditions_atomically(
    client, factory, constraints, conditional
):
    series, registry, kind, me, headers = setup_series(client, factory)
    card = bound_document(client, series, registry, kind, headers, constraints, conditional)
    entry = client.post(
        f"/api/v1/number-series/{series['id']}/reserve",
        headers=headers,
        json={"idempotency_key": "reserve-one"},
    ).json()["items"][0]
    denied = client.post(
        f"/api/v1/number-entries/{entry['id']}/assign",
        headers=headers,
        json={"document_id": card["id"], "revision": 1, "idempotency_key": "assign-invalid"},
    )
    assert denied.status_code == 422, denied.text
    assert client.get(f"/api/v1/documents/{card['id']}").json()["reg_number"] is None
    assert (
        client.get(f"/api/v1/number-series/{series['id']}/entries").json()["items"][0]["state"]
        == "reserved"
    )


def test_parallel_reservations_and_same_key_are_serialized(factory, client):
    from concurrent.futures import ThreadPoolExecutor
    from uuid import UUID

    from app.identity.policy import Actor
    from app.numbering.contracts import ReserveInput
    from app.numbering.service import reserve

    series, registry, kind, me, headers = setup_series(client, factory)
    actor = Actor(UUID(me["id"]), "Synthetic", frozenset({"admin"}))
    request = SimpleNamespace(
        client=None, state=SimpleNamespace(request_id="synthetic-concurrency")
    )

    def allocate(key):
        with factory.begin() as db:
            return reserve(
                db, request, actor, UUID(series["id"]), ReserveInput(count=3, idempotency_key=key)
            )

    with ThreadPoolExecutor(max_workers=4) as pool:
        batches = list(pool.map(allocate, ["one", "two", "same", "same"]))
    assert batches[2] == batches[3]
    distinct = [entry["ordinal"] for batch in batches[:3] for entry in batch["items"]]
    assert sorted(distinct) == list(range(1, 10))


def test_intervals_large_step_and_cancelled_slots_never_free(client, factory):
    series, registry, kind, me, headers = setup_series(client, factory, step=1000000)
    endpoint = f"/api/v1/number-series/{series['id']}"
    entry = client.post(
        endpoint + "/reserve", headers=headers, json={"idempotency_key": "reserve-one"}
    ).json()["items"][0]
    assert (
        client.post(
            f"/api/v1/number-entries/{entry['id']}/cancel",
            headers=headers,
            json={"reason": "Synthetic cancellation", "idempotency_key": "cancel"},
        ).status_code
        == 200
    )
    result = client.get(endpoint + "/intervals?from_ordinal=1&to_ordinal=999000001")
    assert result.status_code == 200, result.text
    assert result.json()["intervals"][0] == {
        "from_ordinal": 1,
        "to_ordinal": 1,
        "state": "cancelled",
    }
    assert result.json()["intervals"][1]["state"] == "free"
    assert (
        client.get(endpoint + "/intervals?from_ordinal=1&to_ordinal=1000000001").status_code == 422
    )


def test_replay_redacts_current_field_acl_without_rewriting_receipt(client, factory):
    from uuid import UUID

    from app.api.dependencies import current_actor
    from app.identity.policy import Actor
    from app.numbering.models import NumberRequest

    series, registry, kind, me, headers = setup_series(client, factory)
    card = bound_document(client, series, registry, kind, headers, title_roles=["admin"])
    entry = client.post(
        f"/api/v1/number-series/{series['id']}/reserve",
        headers=headers,
        json={"idempotency_key": "reserve-one"},
    ).json()["items"][0]
    payload = {"document_id": card["id"], "revision": 1, "idempotency_key": "assign-one"}
    endpoint = f"/api/v1/number-entries/{entry['id']}/assign"
    original = client.post(endpoint, headers=headers, json=payload)
    assert original.status_code == 200, original.text
    assert original.json()["document_title"] == "Synthetic numbered document"
    actor = Actor(UUID(me["id"]), "Synthetic now editor", frozenset({"editor"}))
    client.app.dependency_overrides[current_actor] = lambda: actor
    try:
        replayed = client.post(endpoint, headers=headers, json=payload)
        assert replayed.status_code == 200 and replayed.json()["document_title"] is None
    finally:
        client.app.dependency_overrides.pop(current_actor, None)
    with factory() as db:
        receipt = db.get(NumberRequest, (UUID(series["id"]), UUID(me["id"]), "assign-one"))
        assert receipt.response["document_title"] == "Synthetic numbered document"


def test_reader_row_acl_applies_to_counts_export_history_and_intervals(client, factory):
    from uuid import UUID

    from app.api.dependencies import current_actor
    from app.identity.models import User
    from app.identity.policy import Actor
    from app.numbering.models import NumberGrant
    from app.registry.models import DocumentACL, RegistryAccess

    series, registry, kind, me, headers = setup_series(client, factory)
    card = bound_document(client, series, registry, kind, headers)
    endpoint = f"/api/v1/number-series/{series['id']}"
    entries = client.post(
        endpoint + "/reserve", headers=headers, json={"count": 2, "idempotency_key": "reserve-two"}
    ).json()["items"]
    assert (
        client.post(
            f"/api/v1/number-entries/{entries[0]['id']}/assign",
            headers=headers,
            json={"document_id": card["id"], "revision": 1, "idempotency_key": "assign-one"},
        ).status_code
        == 200
    )
    with factory.begin() as db:
        viewer = User(display_name="Synthetic reader")
        db.add(viewer)
        db.flush()
        db.add(
            RegistryAccess(
                registry_id=UUID(registry["id"]), user_id=viewer.id, permissions=["read"]
            )
        )
        db.add(
            NumberGrant(
                series_id=UUID(series["id"]),
                user_id=viewer.id,
                permissions=["view", "reserve", "assign"],
            )
        )
        db.add(
            DocumentACL(
                document_id=UUID(card["id"]), user_id=UUID(me["id"]), permissions=["read", "update"]
            )
        )
        actor = Actor(viewer.id, viewer.display_name, frozenset({"viewer"}))
    client.app.dependency_overrides[current_actor] = lambda: actor
    try:
        visible = client.get(endpoint + "/entries").json()
        assert visible["total"] == 1 and visible["items"][0]["owner_id"] is None
        stats = client.get(endpoint + "/stats").json()
        assert stats["counts"]["assigned"] == 0 and stats["counts"]["reserved"] == 1
        assert stats["next_ordinal"] is None
        assert client.get(f"/api/v1/number-entries/{entries[0]['id']}/history").status_code == 404
        exported = client.get(endpoint + "/export?format=csv")
        assert (
            exported.status_code == 200
            and b"P/0001" not in exported.content
            and b"P/0002" in exported.content
        )
        assert client.get(endpoint + "/intervals").status_code == 403
        assert (
            client.post(endpoint + "/reserve", json={"idempotency_key": "viewer"}).status_code
            == 403
        )
    finally:
        client.app.dependency_overrides.pop(current_actor, None)


def test_journal_and_entry_identity_are_immutable_in_database(client, factory):
    from uuid import UUID

    from sqlalchemy import delete, update
    from sqlalchemy.exc import DBAPIError

    from app.numbering.models import NumberEntry, NumberOperation, NumberRequest

    series, registry, kind, me, headers = setup_series(client, factory)
    entry = client.post(
        f"/api/v1/number-series/{series['id']}/reserve",
        headers=headers,
        json={"idempotency_key": "immutable"},
    ).json()["items"][0]
    statements = [
        update(NumberEntry).where(NumberEntry.id == UUID(entry["id"])).values(ordinal=999),
        delete(NumberEntry).where(NumberEntry.id == UUID(entry["id"])),
        update(NumberOperation).values(reason="rewrite"),
        update(NumberRequest).values(payload_hash="0" * 64),
    ]
    for statement in statements:
        with pytest.raises(DBAPIError):
            with factory.begin() as db:
                db.execute(statement)
    assert (
        client.get(f"/api/v1/number-series/{series['id']}/entries").json()["items"][0]["ordinal"]
        == 1
    )


def test_indefinite_policy_and_explicit_expiry_are_preserved(client, factory):
    series, registry, kind, me, headers = setup_series(
        client, factory, allow_indefinite=True, reservation_hours=None
    )
    assert series["reservation_hours"] is None
    endpoint = f"/api/v1/number-series/{series['id']}"
    no_expiry = client.post(
        endpoint + "/reserve", headers=headers, json={"idempotency_key": "indefinite"}
    )
    assert no_expiry.status_code == 200, no_expiry.text
    assert no_expiry.json()["items"][0]["expires_at"] is None
    bounded = client.post(
        endpoint + "/reserve",
        headers=headers,
        json={"idempotency_key": "bounded", "reservation_hours": 1},
    )
    assert bounded.status_code == 200 and bounded.json()["items"][0]["expires_at"] is not None
    assert (
        client.post(
            endpoint + "/reserve",
            headers=headers,
            json={"idempotency_key": "excess", "reservation_hours": 8761},
        ).status_code
        == 422
    )
