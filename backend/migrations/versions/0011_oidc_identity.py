"""Explicit external identities without replacing application user IDs."""

from alembic import op

revision = "0011_oidc_identity"
down_revision = "0010_service_api"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE external_identities (
 id UUID NOT NULL CONSTRAINT pk_external_identities PRIMARY KEY,
 user_id UUID NOT NULL CONSTRAINT fk_external_identities_user_id_users REFERENCES users(id),
 issuer VARCHAR(2048) NOT NULL,
 subject VARCHAR(255) NOT NULL,
 active BOOLEAN NOT NULL,
 created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
 CONSTRAINT uq_external_identities_issuer_subject UNIQUE (issuer, subject)
);
CREATE INDEX ix_external_identities_user_id ON external_identities(user_id);
ALTER TABLE auth_sessions ADD COLUMN external_identity_id UUID
 CONSTRAINT fk_auth_sessions_external_identity_id_external_identities REFERENCES external_identities(id);
CREATE INDEX ix_auth_sessions_external_identity_id ON auth_sessions(external_identity_id);
""")


def downgrade():
    # Empty, unused installations can roll back without discarding any identity.
    # Once a binding has existed, including a revoked one, require a recovery plan.
    op.execute("""
LOCK TABLE external_identities IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM external_identities) THEN
  RAISE EXCEPTION 'External identities require an explicit rollback plan';
 END IF;
END $$;
ALTER TABLE auth_sessions DROP COLUMN external_identity_id;
DROP TABLE external_identities;
""")
