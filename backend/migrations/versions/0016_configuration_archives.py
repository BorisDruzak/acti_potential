"""Reversibly hide retained configuration without rewriting published records."""

import sqlalchemy as sa
from alembic import op

revision = "0016_configuration_archives"
down_revision = "0015_document_collaboration"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "configuration_archives",
        sa.Column("object_type", sa.String(80), primary_key=True),
        sa.Column("object_id", sa.Uuid(), primary_key=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("archived_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM configuration_archives)")):
        raise RuntimeError("Archived configuration exists; restore it explicitly before removing archive metadata")
    op.drop_table("configuration_archives")
