"""Keep draft-number history and freeze registration at publication."""
import sqlalchemy as sa
from alembic import op

revision = "0018_draft_registration_history"
down_revision = "0017_registry_number_claims"
branch_labels = None
depends_on = None


def upgrade():
    # Old entries keep their document link and immutable receipts when a draft
    # selects another number. Only one live reservation may be linked at a time.
    op.drop_constraint("uq_number_entries_document_id", "number_entries", type_="unique")
    op.create_index("uq_number_entries_reserved_document", "number_entries", ["document_id"],
                    unique=True, postgresql_where=sa.text("state='reserved'"))
    op.execute("""
        CREATE FUNCTION acti_lock_published_registration() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.card_state='published' AND (
                NEW.reg_number IS DISTINCT FROM OLD.reg_number OR
                NEW.reg_date IS DISTINCT FROM OLD.reg_date OR
                NEW.card_state IS DISTINCT FROM OLD.card_state
            ) THEN
                RAISE EXCEPTION 'Published registration is immutable'
                    USING ERRCODE='23514', CONSTRAINT='published_registration_locked';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER lock_published_registration BEFORE UPDATE OF reg_number, reg_date, card_state
        ON documents FOR EACH ROW EXECUTE FUNCTION acti_lock_published_registration()
    """)


def downgrade():
    if op.get_bind().scalar(sa.text("""SELECT EXISTS(
        SELECT document_id FROM number_entries WHERE document_id IS NOT NULL
        GROUP BY document_id HAVING count(*)>1)
    """)):
        raise RuntimeError("Draft registration history exists; preserve number links before downgrade")
    op.execute("DROP TRIGGER lock_published_registration ON documents")
    op.execute("DROP FUNCTION acti_lock_published_registration()")
    op.drop_index("uq_number_entries_reserved_document", table_name="number_entries")
    op.create_unique_constraint("uq_number_entries_document_id", "number_entries", ["document_id"])
