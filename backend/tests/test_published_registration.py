"""Publication freezes registration while drafts retain non-reusable number history."""
from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.numbering.models import NumberEntry, NumberRequest
from app.registry.models import Document
from tests.test_card_number_lifecycle import configured_schema, create_card
from tests.test_task004_numbers import setup_series


def registration_setup(client, factory):
    series, registry, kind, me, headers = setup_series(client, factory)
    version = client.get(f"/api/v1/document-types/{kind['id']}/versions").json()[0]
    schema = {"sections": [{"key": "main", "title": "Main", "fields": [
        {"key": "number", "label": "Number", "type": "text", "system": "reg_number"},
        {"key": "date", "label": "Date", "type": "date", "system": "reg_date"},
        {"key": "title", "label": "Title", "type": "text", "system": "title"},
    ]}]}
    assert client.put(f"/api/v1/form-schemas/{version['id']}", headers=headers,
                      json={"schema": schema}).status_code == 200
    assert client.post(f"/api/v1/form-schemas/{version['id']}/publish", headers=headers,
                       json={}).status_code == 200
    return series, registry, kind, me, headers


@pytest.mark.parametrize("key,value", [("number", "Changed/42"), ("date", "2026-10-19")])
def test_published_manual_registration_rejects_changes_without_advancing_revision(
    client, factory, key, value,
):
    _, registry, kind, _, headers = registration_setup(client, factory)
    card = create_card(client, registry, kind, headers, card_state="published",
                       values={"number": "Original/42", "date": "2026-10-18", "title": "Original"}).json()
    endpoint = f"/api/v1/documents/{card['id']}"
    rejected = client.put(endpoint, headers=headers, json={"revision": 1, "values": {key: value}})
    assert rejected.status_code == 403, rejected.text
    assert rejected.json()["error"]["code"] == "registration_locked"
    current = client.get(endpoint).json()
    assert current["revision"] == 1 and current["number_locked"] is True
    assert current["reg_number"] == "Original/42" and current["reg_date"] == "2026-10-18"
    unchanged = client.put(endpoint, headers=headers, json={"revision": 1, "values": {
        "number": "Original/42", "date": "2026-10-18", "title": "Other fields remain editable",
    }})
    assert unchanged.status_code == 200, unchanged.text


def test_draft_replaces_reservation_and_publishes_final_values_without_reusing_old_numbers(client, factory):
    _, registry, kind, _, headers = registration_setup(client, factory)
    next_url = f"/api/v1/registries/{registry['id']}/next-number"
    first = client.post(next_url, headers=headers, json={
        "document_type_id": kind["id"], "idempotency_key": "draft-first",
    }).json()
    card = create_card(client, registry, kind, headers, values={
        "number": first["number"], "date": "2026-10-18", "title": "Draft",
    }, number_entry_id=first["number_entry_id"]).json()
    assert card["number_locked"] is False
    endpoint = f"/api/v1/documents/{card['id']}"
    with factory() as db:
        receipts = {(row.series_id, row.actor_id, row.key): row.response for row in db.scalars(select(NumberRequest))}
    manual = client.put(endpoint, headers=headers, json={"revision": 1, "values": {
        "number": "2", "date": "2026-10-19",
    }})
    assert manual.status_code == 200, manual.text
    assert manual.json()["card_state"] == "draft" and manual.json()["number_entry_id"] is None
    for occupied in [first["number"], "2"]:
        denied = create_card(client, registry, kind, headers, values={"number": occupied, "date": "2026-10-19"})
        assert denied.status_code == 409, denied.text
    replacement = client.post(next_url, headers=headers, json={
        "document_type_id": kind["id"], "document_id": card["id"], "revision": 2,
        "idempotency_key": "draft-replacement",
    })
    assert replacement.status_code == 200, replacement.text
    replacement = replacement.json()
    assert replacement["number"] == "3"
    published = client.put(endpoint, headers=headers, json={"revision": 2,
        "values": {"number": replacement["number"], "date": "2026-10-20"},
        "number_entry_id": replacement["number_entry_id"], "card_state": "published",
    })
    assert published.status_code == 200, published.text
    assert published.json()["number_locked"] is True
    assert published.json()["number_entry_id"] == replacement["number_entry_id"]
    assert published.json()["reg_date"] == "2026-10-20"
    with factory() as db:
        old = db.get(NumberEntry, UUID(first["number_entry_id"]))
        current = db.get(NumberEntry, UUID(replacement["number_entry_id"]))
        assert old.state == "cancelled" and current.state == "assigned"
        assert old.document_id == current.document_id == UUID(card["id"])
        assert old.number_string == first["number"]
        for identity, receipt in receipts.items():
            assert db.get(NumberRequest, identity).response == receipt
    for key, value in [("number", "4"), ("date", "2026-10-21")]:
        assert client.put(endpoint, headers=headers, json={"revision": 3, "values": {key: value}}).status_code == 403
    # Downgrading must never erase journal links to make an obsolete constraint fit.
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    with factory.kw["bind"].begin() as connection:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        revision_before = connection.scalar(text("SELECT version_num FROM alembic_version"))
        with pytest.raises(RuntimeError, match="Scoped registration claims exist"), connection.begin_nested():
            command.downgrade(config, "0017_registry_number_claims")
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == revision_before


def test_draft_number_cannot_be_stolen_by_manual_creation_or_another_card_token(client, factory):
    _, registry, kind, _, headers = registration_setup(client, factory)
    result = client.post(f"/api/v1/registries/{registry['id']}/next-number", headers=headers, json={
        "document_type_id": kind["id"], "idempotency_key": "owned-draft",
    }).json()
    card = create_card(client, registry, kind, headers, values={"number": result["number"]},
                       number_entry_id=result["number_entry_id"])
    assert card.status_code == 201, card.text
    for token in [None, result["number_entry_id"]]:
        other = create_card(client, registry, kind, headers, values={"number": result["number"]},
                            **({"number_entry_id": token} if token else {}))
        assert other.status_code == 409, other.text


def test_published_blank_registration_cannot_be_filled_through_number_book_or_next_number(client, factory):
    series, registry, kind, _, headers = registration_setup(client, factory)
    card = create_card(client, registry, kind, headers, card_state="published").json()
    reserve = client.post(f"/api/v1/number-series/{series['id']}/reserve", headers=headers,
                          json={"idempotency_key": "outside-card"}).json()["items"][0]
    denied = client.post(f"/api/v1/number-entries/{reserve['id']}/assign", headers=headers, json={
        "document_id": card["id"], "revision": 1, "idempotency_key": "published-blank",
    })
    assert denied.status_code == 403, denied.text
    denied = client.post(f"/api/v1/registries/{registry['id']}/next-number", headers=headers, json={
        "document_type_id": kind["id"], "document_id": card["id"], "revision": 1,
        "idempotency_key": "published-next",
    })
    assert denied.status_code == 403, denied.text


@pytest.mark.parametrize("key,value", [("reg_number", "SQL/changed"), ("reg_date", date(2026, 10, 21))])
def test_database_guard_rejects_direct_changes_to_published_registration(client, factory, key, value):
    _, registry, kind, _, headers = registration_setup(client, factory)
    card = create_card(client, registry, kind, headers, card_state="published", values={
        "number": "SQL/42", "date": "2026-10-18",
    }).json()
    with pytest.raises(IntegrityError), factory.begin() as db:
        setattr(db.get(Document, UUID(card["id"])), key, value)
        db.flush()


def test_registration_lock_migration_preserves_all_existing_rows(client, factory):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect, text

    _, registry, kind, _, headers = registration_setup(client, factory)
    result = client.post(f"/api/v1/registries/{registry['id']}/next-number", headers=headers, json={
        "document_type_id": kind["id"], "idempotency_key": "migration-reservation",
    }).json()
    response = create_card(client, registry, kind, headers, values={
        "number": result["number"], "date": "2026-10-18",
    }, number_entry_id=result["number_entry_id"])
    assert response.status_code == 201, response.text
    with factory.kw["bind"].begin() as connection:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        tables = sorted(set(inspect(connection).get_table_names()) - {"alembic_version"})

        def snapshot():
            return {table: connection.execute(text(
                f'SELECT count(*), md5(string_agg(to_jsonb(t)::text, \'\' ORDER BY to_jsonb(t)::text)) '
                f'FROM "{table}" t'
            )).one() for table in tables}

        before = snapshot()
        revision_before = connection.scalar(text("SELECT version_num FROM alembic_version"))
        with pytest.raises(RuntimeError, match="Scoped registration claims exist"), connection.begin_nested():
            command.downgrade(config, "0017_registry_number_claims")
        assert snapshot() == before
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == revision_before


def test_bound_draft_can_replace_cleared_number_without_replaying_cancelled_reservation(client, factory):
    series, registry, kind, _, headers = setup_series(client, factory)
    configured_schema(client, series, kind, headers)
    card = create_card(client, registry, kind, headers).json()
    endpoint = f"/api/v1/documents/{card['id']}"
    replaced = client.put(endpoint, headers=headers, json={"revision": 1, "values": {"number": ""}})
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["reg_number"] != card["reg_number"]
    restored = client.put(endpoint, headers=headers, json={
        "revision": 2, "values": {"number": "Manual/final"}, "card_state": "published",
    })
    assert restored.status_code == 200, restored.text
    assert restored.json()["reg_number"] == "Manual/final"
    assert restored.json()["card_state"] == "published"
    with factory() as db:
        entries = db.scalars(select(NumberEntry).where(NumberEntry.document_id == UUID(card["id"]))).all()
        assert len(entries) == 2 and all(entry.state == "cancelled" for entry in entries)
