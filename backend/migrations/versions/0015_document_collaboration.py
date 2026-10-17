"""Retain logically deleted cards and all immutable document dependencies."""

import sqlalchemy as sa
from alembic import op

revision = "0015_document_collaboration"
down_revision = "0014_people_selection"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("deleted_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True))
    op.add_column("documents", sa.Column("deleted_reason", sa.Text(), nullable=True))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM documents WHERE deleted_at IS NOT NULL)")):
        raise RuntimeError("Deleted cards exist; restore a backup instead of reviving retained tombstones")
    op.drop_column("documents", "deleted_reason")
    op.drop_column("documents", "deleted_by")
    op.drop_column("documents", "deleted_at")
