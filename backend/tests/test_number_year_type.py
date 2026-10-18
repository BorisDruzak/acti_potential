from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.registry.models import (
    Document,
    DocumentType,
    FormSchema,
    ReferenceItem,
    ReferenceSet,
)
from tests.test_card_number_lifecycle import create_card
from tests.test_registry_number_uniqueness import manual_setup


def scope_setup(client, factory):
    _, registry, kind, me, headers = manual_setup(client, factory)
    with factory.begin() as db:
        ref = ReferenceSet(
            registry_id=UUID(registry["id"]), code="document_types", name="Act types"
        )
        db.add(ref)
        db.flush()
        types = [
            ReferenceItem(reference_set_id=ref.id, label=label)
            for label in ["Order", "Resolution"]
        ]
        db.add_all(types)
        db.flush()
        version = db.scalar(
            select(FormSchema)
            .where(FormSchema.document_type_id == UUID(kind["id"]))
            .order_by(FormSchema.version.desc())
        )
        fields = version.schema_data["sections"][0]["fields"]
        db.add(
            FormSchema(
                document_type_id=version.document_type_id,
                version=version.version + 1,
                state="published",
                schema_data={
                    "sections": [
                        {
                            "key": "main",
                            "title": "Main",
                            "fields": [
                                *fields,
                                {
                                    "key": "date",
                                    "label": "Registration date",
                                    "type": "date",
                                    "system": "reg_date",
                                },
                                {
                                    "key": "type_id",
                                    "label": "Act type",
                                    "type": "reference",
                                    "reference_code": "document_types",
                                },
                            ],
                        }
                    ]
                },
            )
        )
        ids = [str(t.id) for t in types]
    return registry, kind, me, headers, ids


def test_number_scope_allows_other_year_and_business_type(client, factory):
    registry, kind, _, headers, types = scope_setup(client, factory)

    def create(day, act_type):
        return create_card(
            client,
            registry,
            kind,
            headers,
            values={"number": "S/42", "date": day, "type_id": act_type},
        )

    assert create("2025-12-15", types[0]).status_code == 201
    assert create("2026-01-12", types[0]).status_code == 201
    assert create("2026-01-12", types[1]).status_code == 201
    assert create("2026-09-12", types[0]).status_code == 409


def test_scope_has_one_concurrent_winner_and_sql_guard(client, factory):
    registry, kind, me, headers, types = scope_setup(client, factory)

    def create(_):
        return create_card(
            client,
            registry,
            kind,
            headers,
            values={"number": "Concurrent", "date": "2026-01-01", "type_id": types[0]},
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(create, range(3)))
    assert sorted(r.status_code for r in responses) == [201, 409, 409]
    winner = next(r.json() for r in responses if r.status_code == 201)
    with pytest.raises(IntegrityError), factory.begin() as db:
        db.add(
            Document(
                registry_id=UUID(registry["id"]),
                document_type_id=UUID(kind["id"]),
                schema_version_id=UUID(winner["schema_version_id"]),
                created_by=UUID(me["id"]),
                reg_number="concurrent",
                reg_date=date(2026, 3, 1),
                custom_data={"type_id": types[0]},
            )
        )


def test_published_business_type_is_immutable(client, factory):
    registry, kind, _, headers, types = scope_setup(client, factory)
    card = create_card(
        client,
        registry,
        kind,
        headers,
        values={"number": "Published", "date": "2026-01-01", "type_id": types[0]},
        card_state="published",
    ).json()
    response = client.put(
        f"/api/v1/documents/{card['id']}",
        headers=headers,
        json={"revision": 1, "values": {"type_id": types[1]}},
    )
    assert response.status_code in {403, 409}
    with factory() as db:
        assert db.get(Document, UUID(card["id"])).custom_data["type_id"] == types[0]


def test_scoped_next_number_and_assignment_reject_other_scope(client, factory):
    registry, kind, _, headers, types = scope_setup(client, factory)
    first = create_card(
        client,
        registry,
        kind,
        headers,
        values={"number": "1", "date": "2025-01-01", "type_id": types[0]},
    )
    assert first.status_code == 201
    response = client.post(
        f"/api/v1/registries/{registry['id']}/next-number",
        headers=headers,
        json={
            "document_type_id": kind["id"],
            "reg_date": "2026-01-01",
            "type_id": types[1],
            "idempotency_key": "scope-one",
        },
    )
    assert response.status_code == 200, response.text
    reserved = response.json()
    assert reserved["number"] == "1"
    wrong = create_card(
        client,
        registry,
        kind,
        headers,
        values={"number": "1", "date": "2026-01-01", "type_id": types[0]},
        number_entry_id=reserved["number_entry_id"],
    )
    assert wrong.status_code in {409, 422}
    right = create_card(
        client,
        registry,
        kind,
        headers,
        values={"number": "1", "date": "2026-01-01", "type_id": types[1]},
        number_entry_id=reserved["number_entry_id"],
    )
    assert right.status_code == 201, right.text


def test_number_book_binds_unknown_scope_once_and_draft_change_keeps_history(
    client, factory
):
    from app.numbering.models import NumberEntry, NumberRequest

    registry, kind, _, headers, types = scope_setup(client, factory)
    initial = client.post(
        f"/api/v1/registries/{registry['id']}/next-number",
        headers=headers,
        json={
            "document_type_id": kind["id"],
            "reg_date": "2025-01-01",
            "type_id": types[0],
            "idempotency_key": "book-series",
        },
    ).json()
    reserved = client.post(
        f"/api/v1/number-series/{initial['series_id']}/reserve",
        headers=headers,
        json={"idempotency_key": "unbound-book"},
    ).json()["items"][0]
    card = create_card(
        client,
        registry,
        kind,
        headers,
        values={"date": "2026-01-01", "type_id": types[0]},
    ).json()
    assigned = client.post(
        f"/api/v1/number-entries/{reserved['id']}/assign",
        headers=headers,
        json={"document_id": card["id"], "revision": 1, "idempotency_key": "bind-book"},
    )
    assert assigned.status_code == 200, assigned.text
    with factory() as db:
        entry = db.get(NumberEntry, UUID(reserved["id"]))
        assert entry.scope_bound and entry.registration_year == 2026
        receipt = db.scalar(
            select(NumberRequest).where(NumberRequest.key == "unbound-book")
        ).response
    changed = client.put(
        f"/api/v1/documents/{card['id']}",
        headers=headers,
        json={"revision": 2, "values": {"date": "2025-01-01", "type_id": types[1]}},
    )
    assert changed.status_code == 200, changed.text
    with factory() as db:
        assert db.get(NumberEntry, UUID(reserved["id"])).state == "cancelled"
        assert (
            db.scalar(
                select(NumberRequest).where(NumberRequest.key == "unbound-book")
            ).response
            == receipt
        )
    for day, kind_id in [("2026-01-01", types[0]), ("2025-01-01", types[1])]:
        duplicate = create_card(
            client,
            registry,
            kind,
            headers,
            values={"number": reserved["number"], "date": day, "type_id": kind_id},
        )
        assert duplicate.status_code == 409, duplicate.text


def test_upgrade_does_not_invent_scope_for_cancelled_history(factory):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from app.identity.models import User
    from app.numbering.models import NumberSeries
    from app.registry.models import Registry

    with factory.kw["bind"].begin() as connection:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        command.downgrade(config, "0018_draft_registration_history")
        with (
            Session(bind=connection, join_transaction_mode="create_savepoint") as db,
            db.begin(),
        ):
            user = User(display_name="Migration scope")
            registry = Registry(code="scope-upgrade", name="Scope upgrade")
            kind = DocumentType(code="scope-upgrade", name="Scope upgrade")
            db.add_all([user, registry, kind])
            db.flush()
            schema = FormSchema(document_type_id=kind.id, version=1, state="published")
            series = NumberSeries(
                code="scope-upgrade",
                name="Scope upgrade",
                registry_id=registry.id,
                document_type_ids=[str(kind.id)],
            )
            db.add_all([schema, series])
            db.flush()
            card = Document(
                registry_id=registry.id,
                document_type_id=kind.id,
                schema_version_id=schema.id,
                created_by=user.id,
                reg_number="New",
                reg_date=date(2026, 1, 1),
            )
            db.add(card)
            db.flush()
            for number, state in [("Old", "cancelled"), ("New", "reserved")]:
                db.execute(
                    text("""INSERT INTO number_entries(id,series_id,period_key,ordinal,number_string,state,owner_id,document_id)
                    VALUES(:id,:series,'v3',:ordinal,:number,:state,:owner,:doc)"""),
                    {
                        "id": uuid4(),
                        "series": series.id,
                        "ordinal": 1 if number == "Old" else 2,
                        "number": number,
                        "state": state,
                        "owner": user.id,
                        "doc": card.id,
                    },
                )
        before = (
            connection.execute(
                text("SELECT to_jsonb(e) FROM number_entries e ORDER BY ordinal")
            )
            .scalars()
            .all()
        )
        command.upgrade(config, "head")
        after = (
            connection.execute(
                text(
                    "SELECT to_jsonb(e)-'registration_year'-'registration_type_key'-'scope_bound' FROM number_entries e ORDER BY ordinal"
                )
            )
            .scalars()
            .all()
        )
        assert before == after
        assert connection.execute(
            text(
                "SELECT registration_year,scope_bound FROM number_entries ORDER BY ordinal"
            )
        ).all() == [(0, False), (2026, True)]
