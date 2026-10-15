"""Add universal reference descriptions, hierarchy and validity without changing IDs."""

import sqlalchemy as sa
from alembic import op

revision = "0003_reference_details"
down_revision = "0002_schema_engine"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "reference_sets", sa.Column("description", sa.Text(), nullable=False, server_default="")
    )
    for column in (
        sa.Column("code", sa.String(100), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
    ):
        op.add_column("reference_items", column)
    op.create_foreign_key(
        "fk_reference_parent", "reference_items", "reference_items", ["parent_id"], ["id"]
    )
    op.create_index("ix_reference_items_parent_id", "reference_items", ["parent_id"])
    op.create_index(
        "uq_reference_item_code",
        "reference_items",
        ["reference_set_id", "code"],
        unique=True,
        postgresql_where=sa.text("code <> ''"),
    )
    op.create_check_constraint(
        "reference_validity",
        "reference_items",
        "valid_from IS NULL OR valid_until IS NULL OR valid_from <= valid_until",
    )


def downgrade():
    op.drop_constraint("reference_validity", "reference_items", type_="check")
    op.drop_index("uq_reference_item_code", "reference_items")
    op.drop_index("ix_reference_items_parent_id", "reference_items")
    op.drop_constraint("fk_reference_parent", "reference_items", type_="foreignkey")
    for column in ("valid_until", "valid_from", "parent_id", "sort_order", "description", "code"):
        op.drop_column("reference_items", column)
    op.drop_column("reference_sets", "description")
