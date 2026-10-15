"""Scoped service identities, hashed tokens, aggregate metrics and opaque cursors."""

from alembic import op

revision = "0010_service_api"
down_revision = "0009_execution"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE SEQUENCE outbox_delivery_ordinal_seq AS BIGINT;
ALTER TABLE outbox_events ADD COLUMN delivery_ordinal BIGINT;
ALTER TABLE outbox_events ADD CONSTRAINT uq_outbox_events_delivery_ordinal UNIQUE (delivery_ordinal);
WITH published AS (
  SELECT id, row_number() OVER (ORDER BY processed_at, created_at, id) AS ordinal
  FROM outbox_events WHERE state='processed'
)
UPDATE outbox_events e SET delivery_ordinal=p.ordinal FROM published p WHERE e.id=p.id;
SELECT setval('outbox_delivery_ordinal_seq',
              GREATEST(COALESCE((SELECT max(delivery_ordinal) FROM outbox_events), 0), 1),
              EXISTS(SELECT 1 FROM outbox_events WHERE delivery_ordinal IS NOT NULL));
CREATE FUNCTION protect_delivery_ordinal() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.delivery_ordinal IS NOT NULL AND NEW.delivery_ordinal IS DISTINCT FROM OLD.delivery_ordinal THEN
    RAISE EXCEPTION 'published event delivery order is immutable';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER outbox_delivery_immutable BEFORE UPDATE OF delivery_ordinal ON outbox_events
FOR EACH ROW EXECUTE FUNCTION protect_delivery_ordinal();
""")
    op.execute("""
CREATE TABLE service_accounts (
	id UUID NOT NULL,
	user_id UUID NOT NULL,
	name VARCHAR(200) NOT NULL,
	state VARCHAR(20) NOT NULL,
	registry_ids JSONB NOT NULL,
	operations JSONB NOT NULL,
	created_by UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_service_accounts PRIMARY KEY (id),
	CONSTRAINT uq_service_accounts_user_id UNIQUE (user_id),
	CONSTRAINT fk_service_accounts_user_id_users FOREIGN KEY(user_id) REFERENCES users (id),
	CONSTRAINT fk_service_accounts_created_by_users FOREIGN KEY(created_by) REFERENCES users (id)
)
""")
    op.execute("""
CREATE TABLE service_tokens (
	id UUID NOT NULL,
	account_id UUID NOT NULL,
	token_hash VARCHAR(64) NOT NULL,
	prefix VARCHAR(30) NOT NULL,
	registry_ids JSONB NOT NULL,
	operations JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	revoked_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_service_tokens PRIMARY KEY (id),
	CONSTRAINT fk_service_tokens_account_id_service_accounts FOREIGN KEY(account_id) REFERENCES service_accounts (id),
	CONSTRAINT uq_service_tokens_token_hash UNIQUE (token_hash)
)
""")
    op.execute("""
CREATE INDEX ix_service_tokens_account_id ON service_tokens (account_id)
""")
    op.execute("""
CREATE TABLE service_event_cursors (
	id UUID NOT NULL,
	token_id UUID NOT NULL,
	scope_hash VARCHAR(64) NOT NULL,
	after_ordinal BIGINT NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_service_event_cursors PRIMARY KEY (id),
	CONSTRAINT fk_service_event_cursors_token_id_service_tokens FOREIGN KEY(token_id) REFERENCES service_tokens (id)
)
""")
    op.execute("""
CREATE INDEX ix_service_event_cursors_expires_at ON service_event_cursors (expires_at)
""")
    op.execute("""
CREATE INDEX ix_service_event_cursors_token_id ON service_event_cursors (token_id)
""")
    op.execute("""
CREATE TABLE api_usage_buckets (
	id UUID NOT NULL,
	day DATE NOT NULL,
	registry_id UUID NOT NULL,
	operation VARCHAR(80) NOT NULL,
	requests INTEGER NOT NULL,
	duration_ms_total INTEGER NOT NULL,
	CONSTRAINT pk_api_usage_buckets PRIMARY KEY (id),
	CONSTRAINT uq_api_usage_bucket UNIQUE (day, registry_id, operation),
	CONSTRAINT fk_api_usage_buckets_registry_id_registries FOREIGN KEY(registry_id) REFERENCES registries (id)
)
""")
    op.execute("""
CREATE INDEX ix_api_usage_buckets_registry_id ON api_usage_buckets (registry_id)
""")
    op.execute("""
CREATE FUNCTION protect_service_token() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' OR (to_jsonb(NEW) - 'revoked_at') IS DISTINCT FROM (to_jsonb(OLD) - 'revoked_at')
     OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at) THEN
    RAISE EXCEPTION 'service token receipts are immutable';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER service_token_immutable BEFORE UPDATE OR DELETE ON service_tokens
FOR EACH ROW EXECUTE FUNCTION protect_service_token();
CREATE FUNCTION disable_service_tokens() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.active AND NOT NEW.active THEN
    UPDATE service_tokens SET revoked_at=now()
      WHERE account_id IN (SELECT id FROM service_accounts WHERE user_id=NEW.id) AND revoked_at IS NULL;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER service_identity_disabled AFTER UPDATE OF active ON users
FOR EACH ROW EXECUTE FUNCTION disable_service_tokens();
""")


def downgrade():
    op.execute("DROP TRIGGER outbox_delivery_immutable ON outbox_events")
    op.execute("DROP FUNCTION protect_delivery_ordinal()")
    op.execute("ALTER TABLE outbox_events DROP COLUMN delivery_ordinal")
    op.execute("DROP SEQUENCE outbox_delivery_ordinal_seq")
    op.execute("DROP TRIGGER service_identity_disabled ON users")
    op.execute("DROP FUNCTION disable_service_tokens()")
    op.execute("DROP TRIGGER service_token_immutable ON service_tokens")
    op.execute("DROP FUNCTION protect_service_token()")
    for table in (
        "service_event_cursors",
        "api_usage_buckets",
        "service_tokens",
        "service_accounts",
    ):
        op.drop_table(table)
