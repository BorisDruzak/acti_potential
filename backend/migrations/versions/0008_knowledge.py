"""Versioned templates, immutable practice and anonymous usage buckets."""

from alembic import op

revision = "0008_knowledge"
down_revision = "0007_process_knowledge"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE knowledge_templates (
	id UUID NOT NULL,
	registry_id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_by UUID NOT NULL,
	visibility VARCHAR(20) NOT NULL,
	lifecycle VARCHAR(20) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_knowledge_templates PRIMARY KEY (id),
	CONSTRAINT template_access CHECK (visibility IN ('private','registry') AND lifecycle IN ('active','archived')),
	CONSTRAINT fk_knowledge_templates_registry_id_registries FOREIGN KEY(registry_id) REFERENCES registries (id),
	CONSTRAINT fk_knowledge_templates_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id),
	CONSTRAINT fk_knowledge_templates_created_by_users FOREIGN KEY(created_by) REFERENCES users (id)
)
    """)
    op.execute(
        "CREATE INDEX ix_knowledge_templates_registry_id ON knowledge_templates (registry_id)"
    )
    op.execute("""
CREATE TABLE knowledge_template_versions (
	id UUID NOT NULL,
	template_id UUID NOT NULL,
	version INTEGER NOT NULL,
	revision INTEGER NOT NULL,
	state VARCHAR(20) NOT NULL,
	data JSONB NOT NULL,
	text TEXT NOT NULL,
	search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('russian'::regconfig, text)) STORED NOT NULL,
	published_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_knowledge_template_versions PRIMARY KEY (id),
	CONSTRAINT uq_template_versions_template_version UNIQUE (template_id, version),
	CONSTRAINT template_version_state CHECK (state IN ('draft','published') AND revision > 0 AND version > 0),
	CONSTRAINT fk_knowledge_template_versions_template_id_knowledge_templates FOREIGN KEY(template_id) REFERENCES knowledge_templates (id)
)
    """)
    op.execute(
        "CREATE INDEX ix_knowledge_template_versions_template_id ON knowledge_template_versions (template_id)"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_template_versions_trigram ON knowledge_template_versions USING gin (text gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_template_versions_vector ON knowledge_template_versions USING gin (search_vector)"
    )
    op.execute("""
CREATE TABLE practice_cases (
	id UUID NOT NULL,
	registry_id UUID NOT NULL,
	owner_id UUID NOT NULL,
	created_by UUID NOT NULL,
	visibility VARCHAR(20) NOT NULL,
	lifecycle VARCHAR(20) NOT NULL,
	state VARCHAR(20) NOT NULL,
	revision INTEGER NOT NULL,
	data JSONB NOT NULL,
	text TEXT NOT NULL,
	search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('russian'::regconfig, text)) STORED NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	published_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_practice_cases PRIMARY KEY (id),
	CONSTRAINT practice_case_state CHECK (state IN ('draft','published') AND revision > 0),
	CONSTRAINT practice_case_access CHECK (visibility IN ('private','registry') AND lifecycle IN ('active','archived')),
	CONSTRAINT fk_practice_cases_registry_id_registries FOREIGN KEY(registry_id) REFERENCES registries (id),
	CONSTRAINT fk_practice_cases_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id),
	CONSTRAINT fk_practice_cases_created_by_users FOREIGN KEY(created_by) REFERENCES users (id)
)
    """)
    op.execute("CREATE INDEX ix_practice_cases_registry_id ON practice_cases (registry_id)")
    op.execute(
        "CREATE INDEX ix_practice_cases_trigram ON practice_cases USING gin (text gin_trgm_ops)"
    )
    op.execute("CREATE INDEX ix_practice_cases_vector ON practice_cases USING gin (search_vector)")
    op.execute("""
CREATE TABLE practice_links (
	id UUID NOT NULL,
	case_id UUID NOT NULL,
	ordinal INTEGER NOT NULL,
	data JSONB NOT NULL,
	CONSTRAINT pk_practice_links PRIMARY KEY (id),
	CONSTRAINT fk_practice_links_case_id_practice_cases FOREIGN KEY(case_id) REFERENCES practice_cases (id)
)
    """)
    op.execute("CREATE INDEX ix_practice_links_case_id ON practice_links (case_id)")
    op.execute("""
CREATE TABLE knowledge_curations (
	id UUID NOT NULL,
	case_id UUID,
	template_id UUID,
	action VARCHAR(30) NOT NULL,
	reason TEXT NOT NULL,
	actor_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_knowledge_curations PRIMARY KEY (id),
	CONSTRAINT fk_knowledge_curations_case_id_practice_cases FOREIGN KEY(case_id) REFERENCES practice_cases (id),
	CONSTRAINT fk_knowledge_curations_template_id_knowledge_templates FOREIGN KEY(template_id) REFERENCES knowledge_templates (id),
	CONSTRAINT fk_knowledge_curations_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id)
)
    """)
    op.execute("CREATE INDEX ix_knowledge_curations_case_id ON knowledge_curations (case_id)")
    op.execute(
        "CREATE INDEX ix_knowledge_curations_template_id ON knowledge_curations (template_id)"
    )
    op.execute("""
CREATE TABLE knowledge_usage (
	id UUID NOT NULL,
	day DATE NOT NULL,
	registry_id UUID NOT NULL,
	object_id UUID NOT NULL,
	action VARCHAR(30) NOT NULL,
	count INTEGER NOT NULL,
	CONSTRAINT pk_knowledge_usage PRIMARY KEY (id),
	CONSTRAINT uq_knowledge_usage_bucket UNIQUE (day, registry_id, object_id, action),
	CONSTRAINT fk_knowledge_usage_registry_id_registries FOREIGN KEY(registry_id) REFERENCES registries (id)
)
    """)
    op.execute("CREATE INDEX ix_knowledge_usage_registry_id ON knowledge_usage (registry_id)")

    op.execute("""
      CREATE FUNCTION protect_knowledge_version() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF OLD.state = 'published' THEN RAISE EXCEPTION 'Published knowledge version is immutable'; END IF;
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
      END $$;
      CREATE TRIGGER knowledge_version_immutable BEFORE UPDATE OR DELETE ON knowledge_template_versions
      FOR EACH ROW EXECUTE FUNCTION protect_knowledge_version();
      CREATE FUNCTION protect_practice_case() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF OLD.state = 'published' AND (TG_OP = 'DELETE' OR
          (to_jsonb(NEW) - 'visibility' - 'lifecycle' - 'search_vector') IS DISTINCT FROM (to_jsonb(OLD) - 'visibility' - 'lifecycle' - 'search_vector'))
        THEN RAISE EXCEPTION 'Published practice content is immutable'; END IF;
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
      END $$;
      CREATE TRIGGER practice_case_immutable BEFORE UPDATE OR DELETE ON practice_cases
      FOR EACH ROW EXECUTE FUNCTION protect_practice_case();
      CREATE FUNCTION protect_practice_link() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE identity uuid;
      BEGIN
        identity := CASE WHEN TG_OP = 'DELETE' THEN OLD.case_id ELSE NEW.case_id END;
        PERFORM id FROM practice_cases WHERE id = identity FOR UPDATE;
        IF EXISTS (SELECT 1 FROM practice_cases WHERE id = identity AND state = 'published')
          OR (TG_OP = 'UPDATE' AND EXISTS (SELECT 1 FROM practice_cases WHERE id = OLD.case_id AND state = 'published'))
        THEN RAISE EXCEPTION 'Published practice link is immutable'; END IF;
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
      END $$;
      CREATE TRIGGER practice_link_immutable BEFORE INSERT OR UPDATE OR DELETE ON practice_links
      FOR EACH ROW EXECUTE FUNCTION protect_practice_link();
      CREATE FUNCTION protect_knowledge_curation() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'Knowledge curation is immutable'; END $$;
      CREATE TRIGGER knowledge_curation_immutable BEFORE UPDATE OR DELETE ON knowledge_curations
      FOR EACH ROW EXECUTE FUNCTION protect_knowledge_curation();
    """)


def downgrade():
    op.drop_table("knowledge_usage")
    op.drop_table("knowledge_curations")
    op.drop_table("practice_links")
    op.drop_table("practice_cases")
    op.drop_table("knowledge_template_versions")
    op.drop_table("knowledge_templates")
    op.execute(
        "DROP FUNCTION protect_knowledge_curation(); DROP FUNCTION protect_practice_link(); DROP FUNCTION protect_practice_case(); DROP FUNCTION protect_knowledge_version()"
    )
