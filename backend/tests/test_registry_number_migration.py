from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.identity.models import User
from app.numbering.models import NumberSeries
from app.registry.models import Document, DocumentType, FormSchema, Registry


@pytest.mark.parametrize("active_collision", [False, True])
def test_number_claim_upgrade_preserves_retained_duplicates_and_reports_active_conflicts(
    factory, active_collision,
):
    engine = factory.kw["bind"]
    with engine.begin() as connection:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        command.downgrade(config, "0016_configuration_archives")
        with Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False) as db, db.begin():
            user = User(display_name="Synthetic number migration")
            registry = Registry(code="number-migration", name="Synthetic number migration")
            kind = DocumentType(code="number-migration", name="Synthetic number migration")
            db.add_all([user, registry, kind])
            db.flush()
            schema = FormSchema(document_type_id=kind.id, version=1, state="published")
            db.add(schema)
            db.flush()
            cards = []
            for index in range(3):
                card = Document(registry_id=registry.id, document_type_id=kind.id,
                                schema_version_id=schema.id, created_by=user.id,
                                reg_number=[" Retained/42 ", "retained/42", "\u2003RETAINED/42\u2003"][index],
                                deleted_at=None if index == 0 or active_collision else datetime.now(UTC))
                cards.append(card)
                db.add(card)
            db.flush()
            # Previous periods could contain the same rendered reserved number.
            series = NumberSeries(code="number-migration", name="Synthetic number migration",
                                  registry_id=registry.id)
            db.add(series)
            db.flush()
            for period in ["2025", "2026"]:
                # Seed the historical schema independently of today's ORM columns.
                db.execute(text("""INSERT INTO number_entries
                    (id,series_id,period_key,ordinal,number_string,state,owner_id)
                    VALUES(:id,:series,:period,1,'Spent/1','cancelled',:owner)"""),
                    {"id":uuid4(),"series":series.id,"period":period,"owner":user.id})
        before = connection.execute(text("SELECT * FROM documents ORDER BY id")).mappings().all()
        entries = connection.execute(text("SELECT * FROM number_entries ORDER BY id")).mappings().all()
        if active_collision:
            with pytest.raises(RuntimeError, match="Active registration-number duplicates"), connection.begin_nested():
                command.upgrade(config, "0017_registry_number_claims")
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0016_configuration_archives"
            assert connection.execute(text("SELECT * FROM documents ORDER BY id")).mappings().all() == before
            connection.execute(text("UPDATE documents SET deleted_at=now() WHERE id<>:id"), {"id": cards[0].id})
            before = connection.execute(text("SELECT * FROM documents ORDER BY id")).mappings().all()
        command.upgrade(config, "0017_registry_number_claims")
        assert connection.execute(text("SELECT * FROM documents ORDER BY id")).mappings().all() == before
        assert connection.execute(text("SELECT * FROM number_entries ORDER BY id")).mappings().all() == entries
        claims = connection.execute(text("SELECT number_key, document_id, number_entry_id FROM registry_number_claims ORDER BY number_key")).all()
        assert claims == [("retained/42", cards[0].id, None), ("spent/1", None, None)]
