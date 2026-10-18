"""Scope registration occupancy by registration year and business document type."""

import sqlalchemy as sa
from alembic import op

revision = "0019_number_year_type"
down_revision = "0018_draft_registration_history"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE FUNCTION acti_registration_type(kind uuid, attributes jsonb) RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
            SELECT kind::text || CASE
                WHEN jsonb_typeof(attributes->'type_id')='string'
                 AND (attributes->>'type_id') ~* '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
                THEN '/ref:' || lower(attributes->>'type_id') ELSE '' END
        $$
    """)
    op.add_column(
        "number_entries",
        sa.Column(
            "registration_year", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "number_entries",
        sa.Column(
            "registration_type_key", sa.String(128), nullable=False, server_default=""
        ),
    )
    op.add_column(
        "number_entries",
        sa.Column(
            "scope_bound", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    # Only linked cards prove their registration scope. An unlinked historical
    # reservation has no registration date; do not infer it from period/clock.
    op.execute("""
        UPDATE number_entries e SET
            registration_year=coalesce(extract(year FROM d.reg_date)::int,0),
            registration_type_key=acti_registration_type(d.document_type_id,d.custom_data),
            scope_bound=true
        FROM documents d WHERE d.id=e.document_id
          AND e.state IN ('reserved','assigned')
          AND acti_number_key(e.number_string)=acti_number_key(d.reg_number);
        UPDATE number_entries e SET registration_type_key=s.document_type_ids->>0
        FROM number_series s WHERE s.id=e.series_id AND NOT e.scope_bound
          AND jsonb_array_length(s.document_type_ids)=1
    """)
    op.create_table(
        "registration_number_claims",
        sa.Column(
            "registry_id", sa.Uuid(), sa.ForeignKey("registries.id"), primary_key=True
        ),
        sa.Column("registration_year", sa.Integer(), primary_key=True),
        sa.Column("registration_type_key", sa.String(128), primary_key=True),
        sa.Column("number_key", sa.String(255), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id")),
        sa.Column(
            "number_entry_id",
            sa.Uuid(),
            sa.ForeignKey("number_entries.id"),
            unique=True,
        ),
    )
    op.create_index(
        "ix_registration_number_claims_document_id",
        "registration_number_claims",
        ["document_id"],
    )
    op.execute("""
        WITH occupied AS (
            SELECT d.registry_id,coalesce(extract(year FROM d.reg_date)::int,0) AS year,
                acti_registration_type(d.document_type_id,d.custom_data) AS kind,
                acti_number_key(d.reg_number) AS key,d.id AS document_id,
                NULL::uuid AS entry_id,d.deleted_at IS NULL AS active
            FROM documents d WHERE acti_number_key(d.reg_number) IS NOT NULL
            UNION ALL
            SELECT s.registry_id,e.registration_year,e.registration_type_key,
                acti_number_key(e.number_string),e.document_id,e.id,false
            FROM number_entries e JOIN number_series s ON s.id=e.series_id
            UNION ALL
            SELECT c.registry_id,0,'',c.number_key,c.document_id,c.number_entry_id,false
            FROM registry_number_claims c
            WHERE c.number_entry_id IS NULL AND (c.document_id IS NULL OR NOT EXISTS (
                SELECT 1 FROM documents d WHERE d.id=c.document_id
                  AND acti_number_key(d.reg_number)=c.number_key))
        ), grouped AS (
            SELECT registry_id,year,kind,key,
                CASE WHEN count(DISTINCT document_id) FILTER(WHERE active)=1
                     THEN (array_agg(document_id) FILTER(WHERE active))[1]
                     WHEN count(DISTINCT document_id)=1
                     THEN (array_agg(document_id) FILTER(WHERE document_id IS NOT NULL))[1] END AS doc,
                CASE WHEN count(DISTINCT entry_id)=1
                     THEN (array_agg(entry_id) FILTER(WHERE entry_id IS NOT NULL))[1] END AS entry
            FROM occupied GROUP BY registry_id,year,kind,key
        ) INSERT INTO registration_number_claims
            (registry_id,registration_year,registration_type_key,number_key,document_id,number_entry_id)
            SELECT registry_id,year,kind,key,doc,entry FROM grouped;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION acti_claim_document_number() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE key text; year int; kind text; claimed uuid;
        BEGIN
            key:=acti_number_key(NEW.reg_number);
            IF key IS NULL THEN RETURN NEW; END IF;
            year:=coalesce(extract(year FROM NEW.reg_date)::int,0);
            kind:=acti_registration_type(NEW.document_type_id,NEW.custom_data);
            IF TG_OP='UPDATE' AND NEW.registry_id=OLD.registry_id
               AND key IS NOT DISTINCT FROM acti_number_key(OLD.reg_number)
               AND year=coalesce(extract(year FROM OLD.reg_date)::int,0)
               AND kind=acti_registration_type(OLD.document_type_id,OLD.custom_data)
            THEN RETURN NEW; END IF;
            INSERT INTO registration_number_claims
                (registry_id,registration_year,registration_type_key,number_key,document_id)
            VALUES(NEW.registry_id,year,kind,key,NEW.id)
            ON CONFLICT(registry_id,registration_year,registration_type_key,number_key)
            DO UPDATE SET document_id=EXCLUDED.document_id
            WHERE registration_number_claims.document_id=EXCLUDED.document_id
               OR (registration_number_claims.document_id IS NULL AND EXISTS (
                   SELECT 1 FROM number_entries e
                   WHERE e.id=registration_number_claims.number_entry_id AND e.document_id=NEW.id))
            RETURNING document_id INTO claimed;
            IF claimed IS NULL THEN
                RAISE EXCEPTION 'Registration number is occupied for this year and type'
                    USING ERRCODE='23505',CONSTRAINT='uq_registration_number_scope';
            END IF;
            RETURN NEW;
        END $$;
        DROP TRIGGER claim_document_number ON documents;
        CREATE TRIGGER claim_document_number
        AFTER INSERT OR UPDATE OF reg_number,registry_id,reg_date,document_type_id,custom_data
        ON documents FOR EACH ROW EXECUTE FUNCTION acti_claim_document_number();
        CREATE OR REPLACE FUNCTION acti_claim_entry_number() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE registry uuid; key text; claimed uuid;
        BEGIN
            SELECT registry_id INTO registry FROM number_series WHERE id=NEW.series_id;
            key:=acti_number_key(NEW.number_string);
            IF key IS NULL THEN RAISE EXCEPTION 'Number must be nonempty' USING ERRCODE='23514'; END IF;
            INSERT INTO registration_number_claims
                (registry_id,registration_year,registration_type_key,number_key,number_entry_id,document_id)
            VALUES(registry,NEW.registration_year,NEW.registration_type_key,key,NEW.id,NEW.document_id)
            ON CONFLICT(registry_id,registration_year,registration_type_key,number_key) DO UPDATE
                SET number_entry_id=EXCLUDED.number_entry_id,
                    document_id=coalesce(registration_number_claims.document_id,EXCLUDED.document_id)
            WHERE (registration_number_claims.number_entry_id=EXCLUDED.number_entry_id AND
                   (registration_number_claims.document_id IS NULL OR registration_number_claims.document_id=EXCLUDED.document_id))
               OR (registration_number_claims.number_entry_id IS NULL AND registration_number_claims.document_id=EXCLUDED.document_id)
            RETURNING number_entry_id INTO claimed;
            IF claimed IS NULL THEN
                RAISE EXCEPTION 'Registration number is occupied for this year and type'
                    USING ERRCODE='23505',CONSTRAINT='uq_registration_number_scope';
            END IF;
            RETURN NEW;
        END $$;
        CREATE FUNCTION acti_lock_number_scope() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.registration_year<>OLD.registration_year OR NEW.registration_type_key<>OLD.registration_type_key
                OR NEW.scope_bound<>OLD.scope_bound)
                AND NOT (NOT OLD.scope_bound AND NEW.scope_bound AND OLD.document_id IS NULL) THEN
                RAISE EXCEPTION 'Reserved registration scope is immutable' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER lock_number_scope BEFORE UPDATE OF registration_year,registration_type_key,scope_bound
        ON number_entries FOR EACH ROW EXECUTE FUNCTION acti_lock_number_scope();
        DROP TRIGGER claim_entry_number ON number_entries;
        CREATE TRIGGER claim_entry_number AFTER INSERT OR UPDATE OF number_string,series_id,document_id,registration_year,registration_type_key,scope_bound
        ON number_entries FOR EACH ROW EXECUTE FUNCTION acti_claim_entry_number();
        CREATE OR REPLACE FUNCTION acti_lock_published_registration() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.card_state='published' AND (
                NEW.reg_number IS DISTINCT FROM OLD.reg_number OR
                NEW.reg_date IS DISTINCT FROM OLD.reg_date OR
                NEW.card_state IS DISTINCT FROM OLD.card_state OR
                NEW.registry_id IS DISTINCT FROM OLD.registry_id OR
                acti_registration_type(NEW.document_type_id,NEW.custom_data)
                    IS DISTINCT FROM acti_registration_type(OLD.document_type_id,OLD.custom_data)
            ) THEN RAISE EXCEPTION 'Published registration is immutable'
                USING ERRCODE='23514',CONSTRAINT='published_registration_locked'; END IF;
            RETURN NEW;
        END $$;
        DROP TRIGGER lock_published_registration ON documents;
        CREATE TRIGGER lock_published_registration BEFORE UPDATE OF reg_number,reg_date,card_state,registry_id,document_type_id,custom_data
        ON documents FOR EACH ROW EXECUTE FUNCTION acti_lock_published_registration();
    """)


def downgrade():
    # Global occupancy cannot represent numbers newly shared across scopes.
    # Only an unused target may return to the old rule without losing history.
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS(SELECT 1 FROM registration_number_claims)")
    ):
        raise RuntimeError(
            "Scoped registration claims exist; use a reviewed forward migration or restore"
        )
    op.execute("DROP TRIGGER claim_document_number ON documents")
    op.execute("DROP TRIGGER claim_entry_number ON number_entries")
    op.execute("DROP TRIGGER lock_number_scope ON number_entries")
    op.execute("DROP FUNCTION acti_lock_number_scope()")
    op.execute("DROP TRIGGER lock_published_registration ON documents")
    op.execute("""
        CREATE OR REPLACE FUNCTION acti_claim_document_number() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE key text; claimed uuid;
        BEGIN
            key := acti_number_key(NEW.reg_number);
            IF key IS NULL THEN RETURN NEW; END IF;
            IF TG_OP = 'UPDATE' THEN
                IF NEW.registry_id = OLD.registry_id AND key IS NOT DISTINCT FROM acti_number_key(OLD.reg_number)
                THEN RETURN NEW; END IF;
            END IF;
            INSERT INTO registry_number_claims(registry_id, number_key, document_id)
            VALUES (NEW.registry_id, key, NEW.id)
            ON CONFLICT (registry_id, number_key) DO UPDATE SET document_id=EXCLUDED.document_id
            WHERE registry_number_claims.document_id=EXCLUDED.document_id
               OR (registry_number_claims.document_id IS NULL AND EXISTS (
                   SELECT 1 FROM number_entries e WHERE e.id=registry_number_claims.number_entry_id
                   AND e.document_id=NEW.id))
            RETURNING document_id INTO claimed;
            IF claimed IS NULL THEN
                RAISE EXCEPTION 'Registration number is already occupied'
                    USING ERRCODE='23505', CONSTRAINT='uq_registry_number_claim';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER claim_document_number AFTER INSERT OR UPDATE OF reg_number, registry_id
        ON documents FOR EACH ROW EXECUTE FUNCTION acti_claim_document_number()
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION acti_claim_entry_number() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE registry uuid; key text; claimed uuid;
        BEGIN
            SELECT registry_id INTO registry FROM number_series WHERE id=NEW.series_id;
            key := acti_number_key(NEW.number_string);
            IF key IS NULL THEN
                RAISE EXCEPTION 'Number must be nonempty' USING ERRCODE='23514';
            END IF;
            INSERT INTO registry_number_claims(registry_id, number_key, number_entry_id, document_id)
            VALUES (registry, key, NEW.id, NEW.document_id)
            ON CONFLICT (registry_id, number_key) DO UPDATE
                SET number_entry_id=EXCLUDED.number_entry_id,
                    document_id=coalesce(registry_number_claims.document_id, EXCLUDED.document_id)
            WHERE (registry_number_claims.number_entry_id=EXCLUDED.number_entry_id AND
                   (registry_number_claims.document_id IS NULL OR
                    registry_number_claims.document_id=EXCLUDED.document_id))
               OR (registry_number_claims.number_entry_id IS NULL AND
                   registry_number_claims.document_id=EXCLUDED.document_id)
            RETURNING number_entry_id INTO claimed;
            IF claimed IS NULL THEN
                RAISE EXCEPTION 'Registration number is already occupied'
                    USING ERRCODE='23505', CONSTRAINT='uq_registry_number_claim';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER claim_entry_number AFTER INSERT OR UPDATE OF number_string, series_id, document_id
        ON number_entries FOR EACH ROW EXECUTE FUNCTION acti_claim_entry_number()
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION acti_lock_published_registration() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.card_state='published' AND (
                NEW.reg_number IS DISTINCT FROM OLD.reg_number OR
                NEW.reg_date IS DISTINCT FROM OLD.reg_date OR
                NEW.card_state IS DISTINCT FROM OLD.card_state
            ) THEN
                RAISE EXCEPTION 'Published registration is immutable'
                    USING ERRCODE='23514', CONSTRAINT='published_registration_locked';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER lock_published_registration BEFORE UPDATE OF reg_number, reg_date, card_state
        ON documents FOR EACH ROW EXECUTE FUNCTION acti_lock_published_registration()
    """)
    op.drop_index(
        "ix_registration_number_claims_document_id",
        table_name="registration_number_claims",
    )
    op.drop_table("registration_number_claims")
    op.drop_column("number_entries", "scope_bound")
    op.drop_column("number_entries", "registration_type_key")
    op.drop_column("number_entries", "registration_year")
    op.execute("DROP FUNCTION acti_registration_type(uuid,jsonb)")
