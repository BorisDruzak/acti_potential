import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, inspect, text


def test_fixture_refuses_non_test_database():
    from app.db.session import validate_test_database

    with pytest.raises(ValueError):
        validate_test_database("postgresql+psycopg://localhost/acti")
    validate_test_database("postgresql+psycopg://localhost/acti_test")


def test_clean_repeat_and_concurrent_upgrade():
    from app.db.migrate import upgrade_database

    engine = create_engine(os.environ["ACTI_DATABASE_URL"])
    from app.db.session import validate_test_database

    validate_test_database(str(engine.url))
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: upgrade_database(engine), range(2)))
    upgrade_database(engine)
    assert {
        "users",
        "roles",
        "user_roles",
        "local_credentials",
        "auth_sessions",
        "external_identities",
        "audit_events",
        "outbox_events",
        "processed_events",
    } <= set(inspect(engine).get_table_names())
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0017_registry_number_claims"
        assert conn.scalar(text("SELECT count(*) FROM roles")) == 4
    engine.dispose()


def test_publication_upgrade_preserves_legacy_defaults_history_and_number_guards(factory):
    from pathlib import Path
    from uuid import uuid4

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import MetaData, Table
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.orm import Session

    from app.identity.models import User
    from app.numbering.models import NumberEntry, NumberSeries
    from app.registry.models import DocumentType, FormSchema, Registry

    engine = factory.kw["bind"]
    with engine.begin() as conn:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = conn
        command.downgrade(config, "0012_search_retrieval")
        documents = Table("documents", MetaData(), autoload_with=conn)
        history = Table("metadata_revisions", MetaData(), autoload_with=conn)
        with Session(bind=conn, join_transaction_mode="create_savepoint") as db, db.begin():
            user = User(display_name="Synthetic migration owner")
            registry = Registry(code="publication-migration", name="Synthetic migration")
            dtype = DocumentType(code="publication-migration", name="Synthetic migration")
            db.add_all([user, registry, dtype])
            db.flush()
            schema = FormSchema(document_type_id=dtype.id, version=1, state="published")
            db.add(schema)
            db.flush()
            sources = [("okrug", "documents"), ("raion", "documents"),
                       (None, None), ("other", "documents"), ("okrug", "references")]
            identities = [uuid4() for _ in sources]
            for identity, (database, entity) in zip(identities, sources, strict=True):
                conn.execute(documents.insert().values(
                    id=identity, registry_id=registry.id, document_type_id=dtype.id,
                    schema_version_id=schema.id, created_by=user.id, custom_data={},
                    revision=1, current_version=0, source_database=database,
                    source_entity=entity, source_id=str(identity),
                ))
                conn.execute(history.insert().values(
                    id=uuid4(), document_id=identity, revision=1, values={}, created_by=user.id,
                ))
            series = NumberSeries(code="publication-migration", name="Synthetic migration",
                                  registry_id=registry.id)
            db.add(series)
            db.flush()
            assigned = NumberEntry(series_id=series.id, period_key="2026", ordinal=1,
                                   number_string="0001", owner_id=user.id, state="assigned",
                                   document_id=identities[0])
            reserved = NumberEntry(series_id=series.id, period_key="2026", ordinal=2,
                                   number_string="0002", owner_id=user.id, state="reserved")
            db.add_all([assigned, reserved])
            db.flush()
            assigned_id, reserved_id = assigned.id, reserved.id
        before = conn.execute(text("SELECT * FROM number_entries ORDER BY ordinal")).mappings().all()
        command.upgrade(config, "0013_card_publication")
        rows = conn.execute(text("SELECT id, card_state, publication_targets FROM documents")).all()
        actual = {row.id: (row.card_state, row.publication_targets) for row in rows}
        for index, identity in enumerate(identities):
            assert actual[identity] == ("published" if index < 2 else "draft", ["internal"])
        assert all(state is None and targets is None for state, targets in conn.execute(
            text("SELECT card_state, publication_targets FROM metadata_revisions")
        ))
        assert conn.execute(text("SELECT * FROM number_entries ORDER BY ordinal")).mappings().all() == before
        for statement in (
            "UPDATE number_entries SET number_string='CHANGED' WHERE id=:id",
            "UPDATE number_entries SET document_id=:other WHERE id=:id",
            "UPDATE number_entries SET state='reserved' WHERE id=:id",
            "DELETE FROM number_entries WHERE id=:id",
        ):
            with pytest.raises(DBAPIError), conn.begin_nested():
                conn.execute(text(statement), {"id": assigned_id, "other": identities[2]})
        conn.execute(text("UPDATE number_entries SET document_id=:document WHERE id=:id"),
                     {"document": identities[2], "id": reserved_id})
        with pytest.raises(RuntimeError, match="linked draft reservations"), conn.begin_nested():
            command.downgrade(config, "0012_search_retrieval")
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0013_card_publication"
        conn.execute(text("UPDATE number_entries SET state='assigned' WHERE id=:id"),
                     {"id": reserved_id})
        assert conn.scalar(text("SELECT document_id FROM number_entries WHERE id=:id"),
                           {"id": reserved_id}) == identities[2]
