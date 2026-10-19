"""Add browser-owned agent presence, command relay and observed Word state."""

import sqlalchemy as sa
from alembic import op

revision = "0021_office_agent"
down_revision = "0020_office_sync"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "office_agent_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "auth_session_id",
            sa.Uuid(),
            sa.ForeignKey("auth_sessions.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_version", sa.String(32), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "office_agent_tickets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("auth_session_id", sa.Uuid(), sa.ForeignKey("auth_sessions.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("ticket_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exchanged_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_office_agent_tickets_auth_session_id", "office_agent_tickets", ["auth_session_id"]
    )
    op.add_column(
        "office_edit_sessions",
        sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("office_agent_connections.id")),
    )
    op.create_index(
        "ix_office_edit_sessions_connection_id", "office_edit_sessions", ["connection_id"]
    )
    op.add_column("office_edit_sessions", sa.Column("document_open", sa.Boolean()))
    op.add_column("office_edit_sessions", sa.Column("opened_at", sa.DateTime(timezone=True)))
    op.add_column("office_edit_sessions", sa.Column("closed_at", sa.DateTime(timezone=True)))
    op.create_table(
        "office_agent_commands",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "connection_id", sa.Uuid(), sa.ForeignKey("office_agent_connections.id"), nullable=False
        ),
        sa.Column(
            "session_id", sa.Uuid(), sa.ForeignKey("office_edit_sessions.id"), nullable=False
        ),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("error", sa.String(256)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('open','retry','finish','reveal')", name="office_agent_action"
        ),
        sa.CheckConstraint(
            "state IN ('pending','succeeded','failed','expired')", name="office_agent_command_state"
        ),
    )
    for column in ("connection_id", "session_id"):
        op.create_index("ix_office_agent_commands_" + column, "office_agent_commands", [column])


def downgrade():
    op.drop_table("office_agent_commands")
    for column in ("closed_at", "opened_at", "document_open", "connection_id"):
        op.drop_column("office_edit_sessions", column)
    op.drop_table("office_agent_tickets")
    op.drop_table("office_agent_connections")
