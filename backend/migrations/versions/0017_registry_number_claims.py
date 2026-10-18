"""Reserve a single number namespace across manual cards and numbering series.

No source records or historical receipts are rewritten. Ambiguous retained
occupancy gets an ownerless claim: it blocks reuse without inventing ownership.
"""

import sqlalchemy as sa
from alembic import op

revision = "0017_registry_number_claims"
down_revision = "0016_configuration_archives"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(r"""
        CREATE FUNCTION acti_number_key(value text) RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
            SELECT nullif(lower(btrim(value,
                U&' \0009\000a\000b\000c\000d\001c\001d\001e\001f\0085\00a0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200a\2028\2029\202f\205f\3000')), '')
        $$
    """)
    connection = op.get_bind()
    duplicates = connection.execute(sa.text("""
        SELECT registry_id, count(*) AS groups, sum(cards) AS cards FROM (
            SELECT registry_id, acti_number_key(reg_number), count(*) AS cards
            FROM documents WHERE deleted_at IS NULL AND acti_number_key(reg_number) IS NOT NULL
            GROUP BY registry_id, acti_number_key(reg_number) HAVING count(*) > 1
        ) conflicts GROUP BY registry_id ORDER BY registry_id
    """)).mappings().all()
    if duplicates:
        report = "; ".join(f"registry={row['registry_id']} groups={row['groups']} cards={row['cards']}"
                           for row in duplicates)
        raise RuntimeError("Active registration-number duplicates require explicit resolution; " + report)
    op.create_table(
        "registry_number_claims",
        sa.Column("registry_id", sa.Uuid(), sa.ForeignKey("registries.id"), primary_key=True),
        sa.Column("number_key", sa.String(255), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=True),
        sa.Column("number_entry_id", sa.Uuid(), sa.ForeignKey("number_entries.id"), unique=True, nullable=True),
    )
    op.create_index("ix_registry_number_claims_document_id", "registry_number_claims", ["document_id"])
    op.execute("""
        WITH occupied AS (
            SELECT registry_id, acti_number_key(reg_number) AS key, id AS document_id,
                   NULL::uuid AS entry_id, deleted_at IS NULL AS active
            FROM documents WHERE acti_number_key(reg_number) IS NOT NULL
            UNION ALL
            SELECT s.registry_id, acti_number_key(e.number_string), e.document_id,
                   e.id, false
            FROM number_entries e JOIN number_series s ON s.id=e.series_id
            WHERE acti_number_key(e.number_string) IS NOT NULL
        ), grouped AS (
            SELECT registry_id, key,
                   CASE WHEN count(DISTINCT document_id) FILTER (WHERE active)=1
                        THEN (array_agg(document_id) FILTER (WHERE active))[1]
                        WHEN count(DISTINCT document_id)=1
                        THEN (array_agg(document_id) FILTER (WHERE document_id IS NOT NULL))[1]
                   END AS document_id,
                   CASE WHEN count(DISTINCT entry_id)=1
                        THEN (array_agg(entry_id) FILTER (WHERE entry_id IS NOT NULL))[1]
                   END AS entry_id
            FROM occupied GROUP BY registry_id, key
        )
        INSERT INTO registry_number_claims(registry_id, number_key, document_id, number_entry_id)
        SELECT registry_id, key, document_id, entry_id FROM grouped
    """)
    op.execute("""
        CREATE FUNCTION acti_claim_document_number() RETURNS trigger LANGUAGE plpgsql AS $$
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
        CREATE FUNCTION acti_claim_entry_number() RETURNS trigger LANGUAGE plpgsql AS $$
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


def downgrade():
    # Claims retain previously used manual numbers too. Dropping them would
    # silently make those identifiers reusable after rollback.
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM registry_number_claims)")):
        raise RuntimeError("Occupied registration numbers exist; preserve claims before downgrade")
    op.execute("DROP TRIGGER claim_entry_number ON number_entries")
    op.execute("DROP FUNCTION acti_claim_entry_number()")
    op.execute("DROP TRIGGER claim_document_number ON documents")
    op.execute("DROP FUNCTION acti_claim_document_number()")
    op.drop_index("ix_registry_number_claims_document_id", table_name="registry_number_claims")
    op.drop_table("registry_number_claims")
    op.execute("DROP FUNCTION acti_number_key(text)")
