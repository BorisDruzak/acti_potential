from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.numbering.models import (
    NumberCounter,
    NumberEntry,
    NumberGrant,
    NumberOperation,
    NumberRequest,
)
from app.registry.models import Document, MetadataRevision
from tests.test_task004_numbers import bound_document, setup_series


def test_linked_number_event_contract_accepts_only_series_identity():
    from app.platform.contracts import validate_payload

    payload = {"series_id": str(uuid4())}
    validate_payload("number.linked", payload)
    for extra in ({"number": "Do not disclose"}, {"password": "never-store"}):
        with pytest.raises(ValueError):
            validate_payload("number.linked", {**payload, **extra})


def configured_schema(client, series, kind, headers, *, bound=True, **number_options):
    version = client.get(f"/api/v1/document-types/{kind['id']}/versions").json()[0]
    number = {
        "key": "number", "label": "Number", "type": "text", "system": "reg_number",
        **({"number_series_id": series["id"]} if bound else {}), **number_options,
    }
    schema = {"sections": [{"key": "main", "title": "Main", "fields": [
        number, {"key": "title", "label": "Title", "type": "text", "system": "title"},
    ]}]}
    saved = client.put(
        f"/api/v1/form-schemas/{version['id']}", headers=headers, json={"schema": schema}
    )
    assert saved.status_code == 200, saved.text
    published = client.post(
        f"/api/v1/form-schemas/{version['id']}/publish", headers=headers, json={}
    )
    assert published.status_code == 200, published.text
    return schema


def create_card(client, registry, kind, headers, **options):
    return client.post("/api/v1/documents", headers=headers, json={
        "registry_id": registry["id"], "document_type_id": kind["id"],
        "values": {"title": "Draft card"}, **options,
    })


def entry_for(factory, card):
    with factory() as db:
        return db.scalar(select(NumberEntry).where(NumberEntry.document_id == UUID(card["id"])))


def test_draft_reserves_and_publication_finalizes_same_number_once(client, factory):
    from app.platform.models import AuditEvent, OutboxEvent

    series, registry, kind, me, headers = setup_series(client, factory)
    configured_schema(client, series, kind, headers, required=True)
    created = create_card(client, registry, kind, headers)
    assert created.status_code == 201, created.text
    card = created.json()
    assert card["card_state"] == "draft" and card["reg_number"] == "P/0001"
    entry = entry_for(factory, card)
    assert entry.state == "reserved"
    card_url = f"/api/v1/documents/{card['id']}"
    edited = client.put(card_url, headers=headers, json={
        "revision": 1, "values": {"title": "Ready for publication"},
    })
    assert edited.status_code == 200, edited.text
    assert entry_for(factory, card).state == "reserved"
    published = client.put(card_url, headers=headers, json={
        "revision": 2, "values": {}, "card_state": "published",
    })
    assert published.status_code == 200, published.text
    assert published.json()["reg_number"] == card["reg_number"]
    finalized = entry_for(factory, card)
    assert finalized.id == entry.id and finalized.state == "assigned"
    unchanged = client.put(card_url, headers=headers, json={
        "revision": 3, "values": {}, "card_state": "published",
    })
    assert unchanged.status_code == 200, unchanged.text
    assert client.put(card_url, headers=headers, json={
        "revision": 2, "values": {}, "card_state": "published",
    }).status_code == 409
    with factory() as db:
        linked_audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "number.linked"))
        linked_outbox = db.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "number.linked"))
        assert linked_audit.details == {"series_id": series["id"]}
        assert linked_outbox.payload == {"series_id": series["id"]}
        assert db.scalar(select(func.count()).select_from(NumberEntry)) == 1
        assert db.get(NumberCounter, (UUID(series["id"]), "2026")).next_ordinal == 2
        assert db.scalar(select(func.count()).select_from(NumberOperation).where(
            NumberOperation.entry_id == entry.id, NumberOperation.action == "assigned",
        )) == 1
        assert db.scalar(select(func.count()).select_from(MetadataRevision).where(
            MetadataRevision.document_id == UUID(card["id"]),
        )) == 4
        assert db.get(NumberRequest, (
            UUID(series["id"]), UUID(me["id"]), f"card:{card['id']}:publish",
        )).entry_ids == [str(entry.id)]


def test_direct_publication_allocates_once_and_has_one_metadata_revision(client, factory):
    series, registry, kind, me, headers = setup_series(client, factory)
    configured_schema(client, series, kind, headers)
    response = create_card(client, registry, kind, headers, card_state="published")
    assert response.status_code == 201, response.text
    card = response.json()
    assert card["revision"] == 1 and card["reg_number"] == "P/0001"
    assert entry_for(factory, card).state == "assigned"
    assert len(client.get(f"/api/v1/documents/{card['id']}/history").json()) == 1


@pytest.mark.parametrize("card_state,permissions", [
    ("draft", ["view", "assign"]), ("published", ["view", "reserve"]),
])
def test_creation_permission_failure_rolls_back_card_counter_and_receipts(
    client, factory, card_state, permissions,
):
    series, registry, kind, me, headers = setup_series(client, factory)
    configured_schema(client, series, kind, headers)
    with factory.begin() as db:
        db.get(NumberGrant, (UUID(series["id"]), UUID(me["id"]))).permissions = permissions
    denied = create_card(client, registry, kind, headers, card_state=card_state)
    assert denied.status_code == 403, denied.text
    with factory() as db:
        for model in (Document, NumberEntry, NumberCounter, NumberRequest, NumberOperation):
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_publication_requires_assign_permission_and_rolls_back_metadata(client, factory):
    series, registry, kind, me, headers = setup_series(client, factory)
    configured_schema(client, series, kind, headers)
    card = create_card(client, registry, kind, headers).json()
    with factory.begin() as db:
        db.get(NumberGrant, (UUID(series["id"]), UUID(me["id"]))).permissions = ["view", "reserve"]
    denied = client.put(f"/api/v1/documents/{card['id']}", headers=headers, json={
        "revision": 1, "card_state": "published", "values": {"title": "Must roll back"},
    })
    assert denied.status_code == 403, denied.text
    current = client.get(f"/api/v1/documents/{card['id']}").json()
    assert (current["revision"], current["card_state"], current["title"]) == (1, "draft", "Draft card")
    assert entry_for(factory, card).state == "reserved"
    assert len(client.get(f"/api/v1/documents/{card['id']}/history").json()) == 1


@pytest.mark.parametrize("unavailable", ["cancelled", "expired", "elapsed"])
def test_unavailable_reservation_never_gets_replaced_on_publication(
    client, factory, monkeypatch, unavailable,
):
    from app.numbering import service
    from app.registry import card_numbers

    series, registry, kind, me, headers = setup_series(client, factory)
    configured_schema(client, series, kind, headers)
    card = create_card(client, registry, kind, headers).json()
    entry = entry_for(factory, card)

    class AfterExpiry(datetime):
        @classmethod
        def now(cls, tz=None):
            result = entry.expires_at + timedelta(seconds=1)
            return result.astimezone(tz) if tz else result.replace(tzinfo=None)

    if unavailable in {"expired", "elapsed"}:
        monkeypatch.setattr(service, "datetime", AfterExpiry)
        monkeypatch.setattr(card_numbers, "datetime", AfterExpiry)
    if unavailable != "elapsed":
        action = "cancel" if unavailable == "cancelled" else "expire"
        transitioned = client.post(f"/api/v1/number-entries/{entry.id}/{action}", headers=headers, json={
            "reason": "Unavailable draft reservation", "idempotency_key": "unavailable",
        })
        assert transitioned.status_code == 200, transitioned.text
    denied = client.put(f"/api/v1/documents/{card['id']}", headers=headers, json={
        "revision": 1, "values": {"title": "Must roll back"}, "card_state": "published",
    })
    assert denied.status_code == 409, denied.text
    current = client.get(f"/api/v1/documents/{card['id']}").json()
    assert current["card_state"] == "draft" and current["revision"] == 1
    assert current["reg_number"] == entry.number_string and current["title"] == "Draft card"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(NumberEntry)) == 1
        assert db.get(NumberCounter, (UUID(series["id"]), "2026")).next_ordinal == 2


@pytest.mark.parametrize("options,status", [
    ({"constraints": {"pattern": "^[0-9]{4}$"}}, 422),
    ({"editable": False}, 403),
])
def test_auto_number_obeys_form_validation_and_rolls_back_allocation(client, factory, options, status):
    series, registry, kind, me, headers = setup_series(client, factory)
    configured_schema(client, series, kind, headers, **options)
    denied = create_card(client, registry, kind, headers)
    assert denied.status_code == status, denied.text
    with factory() as db:
        for model in (Document, NumberEntry, NumberCounter, NumberRequest):
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_unbound_manual_number_never_allocates_or_changes_on_publication(client, factory):
    series, registry, kind, me, headers = setup_series(client, factory)
    configured_schema(client, series, kind, headers, bound=False)
    response = create_card(client, registry, kind, headers, values={
        "title": "Manual historical number", "number": "Original/42",
    })
    assert response.status_code == 201, response.text
    card = response.json()
    published = client.put(f"/api/v1/documents/{card['id']}", headers=headers, json={
        "revision": 1, "values": {}, "card_state": "published",
    })
    assert published.status_code == 200, published.text
    assert published.json()["reg_number"] == "Original/42"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(NumberEntry)) == 0


def test_existing_manual_bound_number_is_preserved(client, factory):
    series, registry, kind, me, headers = setup_series(client, factory)
    card = bound_document(client, series, registry, kind, headers)
    with factory.begin() as db:
        db.get(Document, UUID(card["id"])).reg_number = "Legacy/42"
    changed = client.put(f"/api/v1/documents/{card['id']}", headers=headers, json={
        "revision": 1, "values": {"title": "Preserved"},
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()["reg_number"] == "Legacy/42"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(NumberEntry)) == 0


def test_explicit_assign_links_draft_as_reserved_and_publication_reuses_it(client, factory):
    series, registry, kind, me, headers = setup_series(client, factory)
    card = bound_document(client, series, registry, kind, headers)
    with factory.begin() as db:
        db.get(Document, UUID(card["id"])).card_state = "draft"
    entry = client.post(f"/api/v1/number-series/{series['id']}/reserve", headers=headers, json={
        "idempotency_key": "explicit-draft-reservation",
    }).json()["items"][0]
    payload = {"document_id": card["id"], "revision": 1, "idempotency_key": "link-draft"}
    assigned = client.post(f"/api/v1/number-entries/{entry['id']}/assign", headers=headers, json=payload)
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["state"] == "reserved"
    assert client.post(
        f"/api/v1/number-entries/{entry['id']}/assign", headers=headers, json=payload,
    ).json() == assigned.json()
    published = client.put(f"/api/v1/documents/{card['id']}", headers=headers, json={
        "revision": 2, "values": {}, "card_state": "published",
    })
    assert published.status_code == 200, published.text
    assert published.json()["reg_number"] == entry["number"]
    finalized = entry_for(factory, card)
    assert str(finalized.id) == entry["id"] and finalized.state == "assigned"


def test_real_registry_new_v3_issuance_uses_existing_bound_series(client, factory):
    from app.registry.models import Registry

    series, registry, kind, me, headers = setup_series(client, factory)
    configured_schema(client, series, kind, headers)
    with factory.begin() as db:
        db.get(Registry, UUID(registry["id"])).code = "okrug"
    created = create_card(client, registry, kind, headers)
    assert created.status_code == 201, created.text
    assert created.json()["reg_number"] == "P/0001"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(NumberEntry)) == 1


def test_assignment_receipt_replay_cannot_reveal_a_now_private_draft(client, factory):
    from app.api.dependencies import current_actor
    from app.identity.policy import Actor

    series, registry, kind, me, headers = setup_series(client, factory)
    card = bound_document(client, series, registry, kind, headers)
    with factory.begin() as db:
        db.get(Document, UUID(card["id"])).card_state = "draft"
    entry = client.post(f"/api/v1/number-series/{series['id']}/reserve", headers=headers, json={
        "idempotency_key": "draft-replay-reserve",
    }).json()["items"][0]
    payload = {"document_id": card["id"], "revision": 1, "idempotency_key": "draft-replay-assign"}
    endpoint = f"/api/v1/number-entries/{entry['id']}/assign"
    assigned = client.post(endpoint, headers=headers, json=payload)
    assert assigned.status_code == 200, assigned.text
    actor = Actor(UUID(me["id"]), "Now reader", frozenset({"user"}))
    client.app.dependency_overrides[current_actor] = lambda: actor
    try:
        hidden = client.post(endpoint, headers=headers, json=payload)
        assert hidden.status_code == 404, hidden.text
    finally:
        client.app.dependency_overrides.pop(current_actor, None)


def test_user_reader_cannot_use_old_number_grants_or_see_privileged_identifiers(client, factory):
    from app.api.dependencies import current_actor
    from app.identity.policy import Actor

    series, registry, kind, me, headers = setup_series(client, factory)
    card = bound_document(client, series, registry, kind, headers)
    endpoint = f"/api/v1/number-series/{series['id']}"
    reserved = client.post(endpoint + "/reserve", headers=headers, json={
        "count": 3, "idempotency_key": "before-role-change",
    })
    assert reserved.status_code == 200, reserved.text
    assigned, cancelled, available = reserved.json()["items"]
    linked = client.post(f"/api/v1/number-entries/{assigned['id']}/assign", headers=headers, json={
        "document_id": card["id"], "revision": 1, "idempotency_key": "before-role-change-assign",
    })
    assert linked.status_code == 200, linked.text
    cancelled_response = client.post(
        f"/api/v1/number-entries/{cancelled['id']}/cancel", headers=headers,
        json={"reason": "Private cancellation justification", "idempotency_key": "before-cancel"},
    )
    assert cancelled_response.status_code == 200, cancelled_response.text
    with factory() as db:
        grant = db.get(NumberGrant, (UUID(series["id"]), UUID(me["id"])))
        assert set(grant.permissions) == {"view", "reserve", "assign", "cancel", "void"}
        initial_operations = db.scalar(select(func.count()).select_from(NumberOperation))
        initial_requests = db.scalar(select(func.count()).select_from(NumberRequest))
    actor = Actor(UUID(me["id"]), "Reader with old grants", frozenset({"user"}))
    client.app.dependency_overrides[current_actor] = lambda: actor
    try:
        series_response = client.get("/api/v1/number-series")
        assert series_response.status_code == 200, series_response.text
        assert series_response.json()[0]["permissions"] == ["view"]
        visible = client.get(endpoint + "/entries")
        assert visible.status_code == 200, visible.text
        assert visible.json()["total"] == 3
        assert all(item["owner_id"] is None for item in visible.json()["items"])
        journal = client.get(f"/api/v1/number-entries/{cancelled['id']}/history")
        assert journal.status_code == 200, journal.text
        assert {item["action"] for item in journal.json()["items"]} == {"reserved", "cancelled"}
        assert all(item["actor_id"] is None and item["reason"] is None
                   for item in journal.json()["items"])
        assert client.get(endpoint + "/stats").json()["next_ordinal"] is None
        assert client.get(endpoint + "/intervals").status_code == 403
        assert client.post(endpoint + "/reserve", headers=headers, json={
            "idempotency_key": "reader-cannot-reserve",
        }).status_code == 403
        assert client.post(f"/api/v1/number-entries/{available['id']}/assign", headers=headers, json={
            "document_id": card["id"], "revision": 2, "idempotency_key": "reader-cannot-assign",
        }).status_code == 403
        for identity, action in (
            (available["id"], "cancel"), (available["id"], "expire"), (assigned["id"], "void"),
        ):
            denied = client.post(f"/api/v1/number-entries/{identity}/{action}", headers=headers, json={
                "reason": "Must not mutate", "idempotency_key": "reader-cannot-" + action,
            })
            assert denied.status_code == 403, denied.text
    finally:
        client.app.dependency_overrides.pop(current_actor, None)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(NumberOperation)) == initial_operations
        assert db.scalar(select(func.count()).select_from(NumberRequest)) == initial_requests
        assert db.get(NumberEntry, UUID(available["id"])).state == "reserved"
        assert db.get(NumberEntry, UUID(assigned["id"])).state == "assigned"
        assert db.get(NumberCounter, (UUID(series["id"]), "2026")).next_ordinal == 4
