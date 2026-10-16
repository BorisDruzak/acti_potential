"""Separate card lifecycle/publication targets and allow linked draft reservations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_card_publication"
down_revision = "0012_search_retrieval"
branch_labels = None
depends_on = None


def number_guard(allow_reserved):
    permitted = "NEW.state NOT IN ('reserved','assigned')" if allow_reserved else "NEW.state <> 'assigned'"
    op.execute(f"""
        CREATE OR REPLACE FUNCTION protect_number_entry() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'number entries cannot be deleted'; END IF;
          IF ROW(NEW.id,NEW.series_id,NEW.period_key,NEW.ordinal,NEW.number_string,NEW.owner_id,NEW.reserved_at,NEW.expires_at)
             IS DISTINCT FROM ROW(OLD.id,OLD.series_id,OLD.period_key,OLD.ordinal,OLD.number_string,OLD.owner_id,OLD.reserved_at,OLD.expires_at)
          THEN RAISE EXCEPTION 'number identity is immutable'; END IF;
          IF OLD.document_id IS NOT NULL AND NEW.document_id IS DISTINCT FROM OLD.document_id
          THEN RAISE EXCEPTION 'number document identity is immutable'; END IF;
          IF NEW.state <> OLD.state AND NOT (
            OLD.state = 'reserved' AND NEW.state IN ('assigned','cancelled','expired')
            OR OLD.state = 'assigned' AND NEW.state = 'void')
          THEN RAISE EXCEPTION 'invalid number transition'; END IF;
          IF NEW.document_id IS DISTINCT FROM OLD.document_id AND {permitted}
          THEN RAISE EXCEPTION 'document requires reserved or assigned state'; END IF;
          RETURN NEW;
        END $$
    """)


def upgrade():
    op.add_column("documents", sa.Column("card_state", sa.String(20), nullable=False, server_default="draft"))
    op.add_column("documents", sa.Column("publication_targets", postgresql.JSONB(), nullable=False,
                                        server_default=sa.text("'[\"internal\"]'::jsonb")))
    op.create_check_constraint("document_card_state", "documents", "card_state IN ('draft','published')")
    op.create_check_constraint("document_publication_targets", "documents", "jsonb_typeof(publication_targets) = 'array' AND publication_targets <@ '[\"internal\",\"shared\"]'::jsonb AND jsonb_array_length(publication_targets) <= 2")
    op.create_index("ix_documents_card_state", "documents", ["card_state"])
    op.create_index("ix_documents_publication_targets", "documents", ["publication_targets"], postgresql_using="gin")
    op.add_column("metadata_revisions", sa.Column("card_state", sa.String(20), nullable=True))
    op.add_column("metadata_revisions", sa.Column("publication_targets", postgresql.JSONB(), nullable=True))
    # Only already imported, source-qualified cards receive controlled legacy defaults.
    # This does not run a source import or infer permission to publish into the shared base.
    op.execute("UPDATE documents SET card_state='published' WHERE source_database IN ('okrug','raion') AND source_entity='documents'")
    number_guard(True)


def downgrade():
    # Returning to the old guard while a draft is linked would strand valid live reservations.
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM number_entries WHERE document_id IS NOT NULL AND state='reserved')")):
        raise RuntimeError("Finalize or cancel linked draft reservations before downgrade")
    number_guard(False)
    op.drop_column("metadata_revisions", "publication_targets")
    op.drop_column("metadata_revisions", "card_state")
    op.drop_index("ix_documents_publication_targets", table_name="documents")
    op.drop_index("ix_documents_card_state", table_name="documents")
    op.drop_constraint("document_publication_targets", "documents", type_="check")
    op.drop_constraint("document_card_state", "documents", type_="check")
    op.drop_column("documents", "publication_targets")
    op.drop_column("documents", "card_state")
