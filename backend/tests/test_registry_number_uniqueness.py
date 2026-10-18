from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.registry.models import Document, Registry
from tests.test_card_number_lifecycle import configured_schema, create_card
from tests.test_task004_numbers import setup_series


def manual_setup(client, factory):
    series, registry, kind, me, headers = setup_series(client, factory)
    configured_schema(client, series, kind, headers, bound=False)
    return series, registry, kind, me, headers


def test_manual_number_is_trimmed_and_unique_across_cards_and_updates(client, factory):
    _, registry, kind, _, headers = manual_setup(client, factory)
    first = create_card(client, registry, kind, headers, values={"number": "  Ab/42  "})
    assert first.status_code == 201, first.text
    assert first.json()["reg_number"] == "Ab/42"
    duplicate = create_card(client, registry, kind, headers, values={"number": "ab/42"})
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["error"]["code"] == "number_conflict"
    second = create_card(client, registry, kind, headers, values={"number": "Other/42"}).json()
    update = client.put(f"/api/v1/documents/{second['id']}", headers=headers,
                        json={"revision": 1, "values": {"number": "AB/42"}})
    assert update.status_code == 409, update.text
    assert client.get(f"/api/v1/documents/{second['id']}").json()["revision"] == 1


def test_parallel_manual_api_creates_have_exactly_one_winner(client, factory):
    _, registry, kind, _, headers = manual_setup(client, factory)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: create_card(
            client, registry, kind, headers, values={"number": "Concurrent/1"}), range(4)))
    assert sorted(row.status_code for row in results) == [201, 409, 409, 409]
    with factory() as db:
        assert len(list(db.scalars(select(Document).where(Document.reg_number == "Concurrent/1")))) == 1


def test_database_guard_and_deleted_cards_keep_numbers_occupied(client, factory):
    _, registry, kind, me, headers = manual_setup(client, factory)
    card = create_card(client, registry, kind, headers, values={"number": "Reserved/1"}).json()
    with factory.begin() as db:
        from datetime import UTC, datetime

        original = db.get(Document, UUID(card["id"]))
        original.deleted_at = datetime.now(UTC)
    assert create_card(client, registry, kind, headers,
                       values={"number": "reserved/1"}).status_code == 409
    with pytest.raises(IntegrityError), factory.begin() as db:
        db.add(Document(registry_id=UUID(registry["id"]), document_type_id=UUID(kind["id"]),
                        schema_version_id=UUID(card["schema_version_id"]),
                        created_by=UUID(me["id"]), reg_number="RESERVED/1"))


@pytest.mark.parametrize("code", ["okrug", "raion"])
def test_real_registry_next_number_skips_manual_and_retained_claims(client, factory, code):
    _, registry, kind, _, headers = manual_setup(client, factory)
    with factory.begin() as db:
        db.get(Registry, UUID(registry["id"])).code = code
    manual = create_card(client, registry, kind, headers, values={"number": "1"})
    assert manual.status_code == 201, manual.text
    endpoint = f"/api/v1/registries/{registry['id']}/next-number"
    payload = {"document_type_id": kind["id"], "idempotency_key": "next-v3"}
    reserved = client.post(endpoint, headers=headers, json=payload)
    assert reserved.status_code == 200, reserved.text
    number = reserved.json()
    assert number["number"] == "2"
    assert client.post(endpoint, headers=headers, json=payload).json() == number
    duplicate = create_card(client, registry, kind, headers, values={"number": "2"})
    assert duplicate.status_code == 409, duplicate.text
    created = create_card(client, registry, kind, headers, values={"number": number["number"]},
                          number_entry_id=number["number_entry_id"], card_state="published")
    assert created.status_code == 201, created.text
    assert created.json()["reg_number"] == "2"
    protected = client.put(f"/api/v1/documents/{created.json()['id']}", headers=headers,
                           json={"revision": 1, "values": {"number": "99"}})
    assert protected.status_code == 403, protected.text


def test_parallel_different_series_and_manual_numbers_share_registry_namespace(client, factory):
    first, registry, kind, _, headers = manual_setup(client, factory)
    second = client.post("/api/v1/number-series", headers=headers, json={
        "code": "second", "name": "Second", "registry_id": registry["id"],
        "document_type_ids": [kind["id"]], "prefix": "P/", "current_period": "2026",
    }).json()
    me = client.get("/api/v1/users/me").json()
    assert client.put(f"/api/v1/number-series/{second['id']}/grants", headers=headers, json={
        "entries": [{"user_id": me["id"], "permissions": ["view", "reserve"]}],
    }).status_code == 200
    def reserve(series):
        return client.post(f"/api/v1/number-series/{series['id']}/reserve", headers=headers,
                           json={"idempotency_key": str(uuid4())})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(reserve, [first, second]))
    assert [r.status_code for r in responses] == [200, 200]
    assert {r.json()["items"][0]["number"] for r in responses} == {"P/0001", "P/0002"}
    assert create_card(client, registry, kind, headers,
                       values={"number": "P/0001"}).status_code == 409


def test_next_number_does_not_save_existing_card_and_attachment_updates_once(client, factory):
    _, registry, kind, _, headers = manual_setup(client, factory)
    card = create_card(client, registry, kind, headers).json()
    endpoint = f"/api/v1/registries/{registry['id']}/next-number"
    payload = {"document_type_id": kind["id"], "document_id": card["id"], "revision": 1,
               "idempotency_key": "unsaved-card"}
    result = client.post(endpoint, headers=headers, json=payload)
    assert result.status_code == 200, result.text
    before = client.get(f"/api/v1/documents/{card['id']}").json()
    assert (before["revision"], before["title"], before["reg_number"]) == (1, "Draft card", None)
    assigned = client.put(f"/api/v1/documents/{card['id']}", headers=headers, json={
        "revision": 1, "values": {"number": result.json()["number"], "title": "Pending edits"},
        "number_entry_id": result.json()["number_entry_id"],
    })
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["number_locked"] is True
    assert assigned.json()["number_entry_id"] == result.json()["number_entry_id"]
    assert (assigned.json()["revision"], assigned.json()["title"]) == (2, "Pending edits")


def test_next_number_idempotency_binds_document_context(client, factory):
    _, registry, kind, _, headers = manual_setup(client, factory)
    first = create_card(client, registry, kind, headers).json()
    second = create_card(client, registry, kind, headers).json()
    endpoint = f"/api/v1/registries/{registry['id']}/next-number"
    payload = {"document_type_id": kind["id"], "document_id": first["id"], "revision": 1,
               "idempotency_key": "same-key"}
    assert client.post(endpoint, headers=headers, json=payload).status_code == 200
    reused = client.post(endpoint, headers=headers, json={**payload, "document_id": second["id"]})
    assert reused.status_code == 409, reused.text
    assert reused.json()["error"]["code"] == "idempotency_conflict"


def test_next_number_accepts_full_length_idempotency_key(client, factory):
    _, registry, kind, _, headers = manual_setup(client, factory)
    response = client.post(f"/api/v1/registries/{registry['id']}/next-number", headers=headers,
                           json={"document_type_id": kind["id"], "idempotency_key": "x" * 120})
    assert response.status_code == 200, response.text


def test_number_scope_is_per_registry_and_manual_bound_input_is_supported(client, factory):
    series, registry, kind, _, headers = setup_series(client, factory)
    configured_schema(client, series, kind, headers)
    manual = create_card(client, registry, kind, headers, values={"number": "Manual/1"})
    assert manual.status_code == 201, manual.text
    assert manual.json()["number_locked"] is False
    # The bound series prevents cross-registry use; a separate unbound form is
    # used below to establish the number uniqueness boundary itself.
    with factory.begin() as db:
        other = Registry(code="other-numbering", name="Other")
        db.add(other)
        db.flush()
        original = db.get(Document, UUID(manual.json()["id"]))
        db.add(Document(registry_id=other.id, document_type_id=original.document_type_id,
                        schema_version_id=original.schema_version_id, created_by=original.created_by,
                        reg_number="MANUAL/1"))


def test_next_number_denies_reader_and_hidden_field_without_allocating(client, factory):
    from app.api.dependencies import current_actor
    from app.identity.policy import Actor
    from app.numbering.models import NumberEntry

    _, registry, kind, me, headers = manual_setup(client, factory)
    endpoint = f"/api/v1/registries/{registry['id']}/next-number"
    payload = {"document_type_id": kind["id"], "idempotency_key": "reader-reserve"}
    client.app.dependency_overrides[current_actor] = lambda: Actor(
        UUID(me["id"]), "Now reader", frozenset({"viewer"}))
    try:
        assert client.post(endpoint, headers=headers, json=payload).status_code == 403
    finally:
        client.app.dependency_overrides.pop(current_actor, None)
    with factory() as db:
        assert list(db.scalars(select(NumberEntry))) == []


def test_mismatched_or_foreign_reservation_never_replaces_manual_number(client, factory):
    _, registry, kind, _, headers = manual_setup(client, factory)
    endpoint = f"/api/v1/registries/{registry['id']}/next-number"
    reserved = client.post(endpoint, headers=headers, json={
        "document_type_id": kind["id"], "idempotency_key": "mismatch",
    }).json()
    mismatch = create_card(client, registry, kind, headers, values={"number": "Other"},
                           number_entry_id=reserved["number_entry_id"])
    assert mismatch.status_code == 422, mismatch.text
    created = create_card(client, registry, kind, headers, values={"number": reserved["number"]},
                          number_entry_id=reserved["number_entry_id"], card_state="published")
    assert created.status_code == 201, created.text
    reused = create_card(client, registry, kind, headers, values={"number": reserved["number"]},
                         number_entry_id=reserved["number_entry_id"])
    assert reused.status_code == 409, reused.text
    other = client.post("/api/v1/registries", headers=headers, json={
        "code": "foreign-token", "name": "Other", "document_type_ids": [kind["id"]],
    })
    assert other.status_code == 201, other.text
    me = client.get("/api/v1/users/me").json()
    assert client.put(f"/api/v1/admin/access/{other.json()['id']}", headers=headers, json={
        "entries": [{"user_id": me["id"], "permissions": ["read", "create", "update"]}],
    }).status_code == 200
    foreign = create_card(client, other.json(), kind, headers,
                          values={"number": reserved["number"]},
                          number_entry_id=reserved["number_entry_id"])
    assert foreign.status_code == 409, foreign.text


def test_parallel_manual_and_automatic_allocation_cannot_duplicate_or_deadlock(client, factory):
    _, registry, kind, _, headers = manual_setup(client, factory)
    endpoint = f"/api/v1/registries/{registry['id']}/next-number"
    def automatic():
        return client.post(endpoint, headers=headers, json={
            "document_type_id": kind["id"], "idempotency_key": "racing-auto",
        })
    with ThreadPoolExecutor(max_workers=2) as pool:
        auto = pool.submit(automatic)
        manual = pool.submit(create_card, client, registry, kind, headers, values={"number": "1"})
        reserved, card = auto.result(timeout=15), manual.result(timeout=15)
    assert reserved.status_code == 200, reserved.text
    assert card.status_code in {201, 409}, card.text
    if card.status_code == 201:
        assert reserved.json()["number"] != card.json()["reg_number"]


def test_hidden_number_field_denies_reservation_and_redacts_number_metadata(client, factory):
    from app.api.dependencies import current_actor
    from app.identity.policy import Actor

    series, registry, kind, me, headers = setup_series(client, factory)
    configured_schema(client, series, kind, headers, bound=False, read_roles=["admin"])
    card = create_card(client, registry, kind, headers).json()
    endpoint = f"/api/v1/registries/{registry['id']}/next-number"
    reserved = client.post(endpoint, headers=headers, json={
        "document_type_id": kind["id"], "idempotency_key": "before-hidden",
    }).json()
    attached = client.put(f"/api/v1/documents/{card['id']}", headers=headers, json={
        "revision": 1, "values": {"number": reserved["number"]},
        "number_entry_id": reserved["number_entry_id"],
    })
    assert attached.status_code == 200, attached.text
    client.app.dependency_overrides[current_actor] = lambda: Actor(
        UUID(me["id"]), "Now editor", frozenset({"editor"}))
    try:
        denied = client.post(endpoint, headers=headers, json={
            "document_type_id": kind["id"], "idempotency_key": "hidden",
        })
        assert denied.status_code == 403, denied.text
        output = client.get(f"/api/v1/documents/{card['id']}").json()
        assert output["number_entry_id"] is None and output["number_locked"] is False
        assert "number" not in output["values"]
    finally:
        client.app.dependency_overrides.pop(current_actor, None)


def test_retained_number_spelling_and_receipt_are_not_normalized_by_unrelated_save(client, factory):
    from app.numbering.models import NumberEntry

    series, registry, kind, me, headers = manual_setup(client, factory)
    card = create_card(client, registry, kind, headers).json()
    with factory.begin() as db:
        old = db.get(Document, UUID(card["id"]))
        old.reg_number = "  Historic/42  "
        db.flush()
        db.add(NumberEntry(series_id=UUID(series["id"]), period_key="2020", ordinal=42,
                           number_string=old.reg_number, document_id=old.id,
                           owner_id=UUID(me["id"]), state="assigned"))
    saved = client.put(f"/api/v1/documents/{card['id']}", headers=headers, json={
        "revision": 1, "values": {"number": "  Historic/42  ", "title": "Unrelated edit"},
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["reg_number"] == "  Historic/42  "
    with factory() as db:
        receipt = db.scalar(select(NumberEntry).where(NumberEntry.document_id == UUID(card["id"])))
        assert receipt.number_string == "  Historic/42  "


def test_default_card_reservation_keeps_draft_editable_after_twenty_four_hours(
    client, factory, monkeypatch,
):
    from datetime import datetime, timedelta

    from app.numbering import service
    from app.numbering.models import NumberEntry, NumberRequest, NumberSeries
    from app.registry import card_numbers

    _, registry, kind, _, headers = manual_setup(client, factory)
    result = client.post(f"/api/v1/registries/{registry['id']}/next-number", headers=headers, json={
        "document_type_id": kind["id"], "idempotency_key": "durable-card-reservation",
    })
    assert result.status_code == 200, result.text
    reservation = result.json()
    created = create_card(client, registry, kind, headers,
                          values={"number": reservation["number"], "title": "Work in progress"},
                          number_entry_id=reservation["number_entry_id"])
    assert created.status_code == 201, created.text
    card = created.json()
    with factory() as db:
        entry = db.get(NumberEntry, UUID(reservation["number_entry_id"]))
        original_receipts = [row.response for row in db.scalars(select(NumberRequest))]

    class ThreeDaysLater(datetime):
        @classmethod
        def now(cls, tz=None):
            result = entry.reserved_at + timedelta(days=3)
            return result.astimezone(tz) if tz else result.replace(tzinfo=None)

    monkeypatch.setattr(service, "datetime", ThreeDaysLater)
    monkeypatch.setattr(card_numbers, "datetime", ThreeDaysLater)
    saved = client.put(f"/api/v1/documents/{card['id']}", headers=headers, json={
        "revision": 1, "values": {"title": "Continued after three days"},
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["title"] == "Continued after three days"
    assert saved.json()["reg_number"] == reservation["number"]
    assert saved.json()["number_entry_id"] == reservation["number_entry_id"]
    with factory() as db:
        current = db.get(NumberEntry, entry.id)
        series = db.get(NumberSeries, current.series_id)
        assert current.state == "reserved" and current.expires_at is None
        assert series.allow_indefinite is True and series.reservation_hours is None
        assert [row.response for row in db.scalars(select(NumberRequest))] == original_receipts
    published = client.put(f"/api/v1/documents/{card['id']}", headers=headers, json={
        "revision": 2, "values": {}, "card_state": "published",
    })
    assert published.status_code == 200, published.text
    assert published.json()["reg_number"] == reservation["number"]
    with factory() as db:
        assert db.get(NumberEntry, entry.id).state == "assigned"
