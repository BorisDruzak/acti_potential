"""Derived source maps for unified retrieval; no duplicate extracted text."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_search_retrieval"
down_revision = "0011_oidc_identity"
branch_labels = None
depends_on = None


def upgrade():
    # Initial 150k pilot demonstrated a full fragment scan for normalized numbers.
    # Existing FTS/trigram indexes cannot answer punctuation-insensitive equality.
    op.execute(
        "CREATE INDEX ix_metadata_fragments_number_fingerprint ON content_metadata_fragments (md5(regexp_replace(replace(lower(text), 'ё', 'е'), '[^[:alnum:]]', '', 'g')))"
    )
    op.add_column(
        "content_index_states",
        sa.Column("index_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.alter_column("content_index_states", "index_version", server_default="2")
    op.create_table(
        "content_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column(
            "extraction_id", sa.Uuid(), sa.ForeignKey("content_extractions.id"), nullable=False
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("ranges", postgresql.JSONB(), nullable=False),
        sa.Column("block_ids", postgresql.JSONB(), nullable=False),
        sa.Column("page", sa.Integer()),
        sa.Column("anchor", sa.String(255), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("algorithm_version", sa.String(30), nullable=False),
        sa.UniqueConstraint("extraction_id", "algorithm_version", "ordinal"),
    )
    op.create_index("ix_content_chunks_document_id", "content_chunks", ["document_id"])
    op.create_index("ix_content_chunks_extraction_id", "content_chunks", ["extraction_id"])
    op.execute(
        "CREATE TRIGGER protect_content_chunks BEFORE UPDATE OR DELETE ON content_chunks FOR EACH ROW EXECUTE FUNCTION protect_document_blocks()"
    )


def downgrade():
    op.drop_index(
        "ix_metadata_fragments_number_fingerprint", table_name="content_metadata_fragments"
    )
    op.drop_table("content_chunks")
    op.drop_column("content_index_states", "index_version")
