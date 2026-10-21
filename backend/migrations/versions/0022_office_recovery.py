"""Add nullable queue telemetry and command-bound local-copy recovery receipts.

Apply before deploying the API that reads these columns. Existing sessions keep
NULL telemetry: an old client has an unknown queue, never a confirmed empty one.
No old request fields become required; response capabilities are additive.
Downgrade refuses recovery commands so their copy receipts cannot be discarded.
"""

import sqlalchemy as sa
from alembic import op

revision = "0022_office_recovery"
down_revision = "0021_office_agent"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("office_edit_sessions", sa.Column("pending_uploads", sa.Integer()))
    op.add_column("office_edit_sessions", sa.Column("error_code", sa.String(24)))
    op.add_column("office_edit_sessions", sa.Column("telemetry_seen_at", sa.DateTime(timezone=True)))
    op.create_check_constraint("office_pending_uploads", "office_edit_sessions", "pending_uploads BETWEEN 0 AND 2")
    op.create_check_constraint("office_sync_error_code", "office_edit_sessions",
        "error_code IN ('network','conflict','reauth','quota','file_busy','word_missing','local_io','server_rejected')")
    op.add_column("office_agent_commands", sa.Column("recovery_sha256", sa.String(64)))
    op.drop_constraint("office_agent_action", "office_agent_commands", type_="check")
    op.create_check_constraint("office_agent_action", "office_agent_commands",
        "action IN ('open','retry','finish','reveal','recover')")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM office_agent_commands WHERE action='recover')")):
        raise RuntimeError("Cannot remove command-bound recovery evidence")
    op.drop_constraint("office_agent_action", "office_agent_commands", type_="check")
    op.create_check_constraint("office_agent_action", "office_agent_commands", "action IN ('open','retry','finish','reveal')")
    op.drop_column("office_agent_commands", "recovery_sha256")
    op.drop_constraint("office_sync_error_code", "office_edit_sessions", type_="check")
    op.drop_constraint("office_pending_uploads", "office_edit_sessions", type_="check")
    for column in ("telemetry_seen_at", "error_code", "pending_uploads"):
        op.drop_column("office_edit_sessions", column)
