"""Add stable originals, immutable binary history and session-bound Office editing."""

import sqlalchemy as sa
from alembic import op

revision = "0020_office_sync"
down_revision = "0019_number_year_type"
branch_labels = None
depends_on = None


def upgrade():
    # No file objects, schema values or metadata histories are rewritten.
    op.create_table(
        "logical_originals",
        sa.Column("id", sa.Uuid(), sa.ForeignKey("document_files.id"), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column(
            "current_file_id",
            sa.Uuid(),
            sa.ForeignKey("document_files.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("revision > 0", name="original_revision"),
    )
    op.create_index("ix_logical_originals_document_id", "logical_originals", ["document_id"])
    op.create_table(
        "binary_revisions",
        sa.Column(
            "original_id", sa.Uuid(), sa.ForeignKey("logical_originals.id"), primary_key=True
        ),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column(
            "file_id", sa.Uuid(), sa.ForeignKey("document_files.id"), nullable=False, unique=True
        ),
    )
    op.create_table(
        "office_edit_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("original_id", sa.Uuid(), sa.ForeignKey("logical_originals.id"), nullable=False),
        sa.Column("auth_session_id", sa.Uuid(), sa.ForeignKey("auth_sessions.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("ticket_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("ticket_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exchanged_at", sa.DateTime(timezone=True)),
        sa.Column("token_hash", sa.String(64), unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_saved_at", sa.DateTime(timezone=True)),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_office_edit_sessions_original_id", "office_edit_sessions", ["original_id"])
    op.create_index(
        "ix_office_edit_sessions_lease_expires_at", "office_edit_sessions", ["lease_expires_at"]
    )
    op.create_table(
        "office_save_receipts",
        sa.Column(
            "session_id", sa.Uuid(), sa.ForeignKey("office_edit_sessions.id"), primary_key=True
        ),
        sa.Column("idempotency_key", sa.String(100), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.execute("""
        INSERT INTO logical_originals(id, document_id, current_file_id, revision)
        SELECT id, document_id, id, 1 FROM document_files
    """)
    op.execute("""
        INSERT INTO binary_revisions(original_id, revision, file_id)
        SELECT id, 1, id FROM document_files
    """)
    op.execute("""
        DO $$ BEGIN
          IF (SELECT count(*) FROM document_files) <> (SELECT count(*) FROM logical_originals)
          THEN RAISE EXCEPTION 'Original backfill integrity failed'; END IF;
        END $$
    """)


def downgrade():
    # Refuse destructive downgrade once a binary history exists.
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM binary_revisions WHERE revision > 1)")):
        raise RuntimeError(
            "Preserve binary history: Office Sync downgrade requires an explicit retention policy"
        )
    for name in (
        "office_save_receipts",
        "office_edit_sessions",
        "binary_revisions",
        "logical_originals",
    ):
        op.drop_table(name)
