"""Versioned descriptive processes with frozen published child records."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007_process_knowledge"
down_revision = "0006_document_relations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "processes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("registry_id", sa.Uuid(), sa.ForeignKey("registries.id"), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("metadata_data", JSONB(), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("registry_id", "code", name="uq_processes_registry_code"),
    )
    op.create_index("ix_processes_registry_id", "processes", ["registry_id"])
    op.create_table(
        "process_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("process_id", sa.Uuid(), sa.ForeignKey("processes.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("metadata_snapshot", JSONB(), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("process_id", "version", name="uq_process_versions_process_version"),
        sa.CheckConstraint("state IN ('draft','published')", name="process_version_state"),
        sa.CheckConstraint("version > 0 AND revision > 0", name="process_version_positive"),
    )
    op.create_index("ix_process_versions_process_id", "process_versions", ["process_id"])
    for table in (
        "process_steps",
        "process_transitions",
        "process_step_sources",
        "process_step_templates",
    ):
        key = "step_key" if table.startswith("process_step_") else "key"
        constraints = (
            []
            if key == "step_key"
            else [sa.UniqueConstraint("version_id", "key", name=f"uq_{table}_version_key")]
        )
        op.create_table(
            table,
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "version_id", sa.Uuid(), sa.ForeignKey("process_versions.id"), nullable=False
            ),
            sa.Column(key, sa.String(100), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("data", JSONB(), nullable=False),
            *constraints,
        )
        op.create_index(f"ix_{table}_version_id", table, ["version_id"])
    op.execute("""
        CREATE FUNCTION protect_process_version() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.state = 'published' THEN RAISE EXCEPTION 'Published process version is immutable'; END IF;
          RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END $$;
        CREATE TRIGGER process_version_immutable BEFORE UPDATE OR DELETE ON process_versions
        FOR EACH ROW EXECUTE FUNCTION protect_process_version();
        CREATE FUNCTION protect_process_child() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE identity uuid;
        BEGIN
          identity := CASE WHEN TG_OP = 'DELETE' THEN OLD.version_id ELSE NEW.version_id END;
          PERFORM id FROM process_versions WHERE id = identity FOR UPDATE;
          IF EXISTS (SELECT 1 FROM process_versions WHERE id = identity AND state = 'published')
             OR (TG_OP = 'UPDATE' AND EXISTS (SELECT 1 FROM process_versions WHERE id = OLD.version_id AND state = 'published'))
          THEN RAISE EXCEPTION 'Published process child is immutable'; END IF;
          RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END $$;
    """)
    for table in (
        "process_steps",
        "process_transitions",
        "process_step_sources",
        "process_step_templates",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_process_child()"
        )


def downgrade():
    for table in (
        "process_step_templates",
        "process_step_sources",
        "process_transitions",
        "process_steps",
    ):
        op.drop_table(table)
    op.drop_table("process_versions")
    op.drop_table("processes")
    op.execute("DROP FUNCTION protect_process_child(); DROP FUNCTION protect_process_version()")
