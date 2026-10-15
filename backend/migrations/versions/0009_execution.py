"""Frozen process execution snapshots and append-only result/history facts."""

from alembic import op

revision = "0009_execution"
down_revision = "0008_knowledge"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE execution_instances (
	id UUID NOT NULL,
	registry_id UUID NOT NULL,
	process_version_id UUID NOT NULL,
	name VARCHAR(300) NOT NULL,
	snapshot JSONB NOT NULL,
	state VARCHAR(20) NOT NULL,
	revision INTEGER NOT NULL,
	created_by UUID NOT NULL,
	assignee_id UUID NOT NULL,
	current_execution_id UUID,
	path JSONB NOT NULL,
	deadline_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_execution_instances PRIMARY KEY (id),
	CONSTRAINT execution_state CHECK (state IN ('pending','active','suspended','completed','cancelled') AND revision > 0),
	CONSTRAINT fk_execution_instances_registry_id_registries FOREIGN KEY(registry_id) REFERENCES registries (id),
	CONSTRAINT fk_execution_instances_process_version_id_process_versions FOREIGN KEY(process_version_id) REFERENCES process_versions (id),
	CONSTRAINT fk_execution_instances_created_by_users FOREIGN KEY(created_by) REFERENCES users (id),
	CONSTRAINT fk_execution_instances_assignee_id_users FOREIGN KEY(assignee_id) REFERENCES users (id)
)
    """)
    op.execute(
        "CREATE INDEX ix_execution_instances_registry_id ON execution_instances (registry_id)"
    )
    op.execute("""
CREATE TABLE step_executions (
	id UUID NOT NULL,
	instance_id UUID NOT NULL,
	step_key VARCHAR(100) NOT NULL,
	attempt INTEGER NOT NULL,
	state VARCHAR(20) NOT NULL,
	assignee_id UUID NOT NULL,
	result_id UUID,
	started_at TIMESTAMP WITH TIME ZONE,
	completed_at TIMESTAMP WITH TIME ZONE,
	deadline_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_step_executions PRIMARY KEY (id),
	CONSTRAINT uq_step_execution_attempt UNIQUE (instance_id, step_key, attempt),
	CONSTRAINT step_execution_state CHECK (state IN ('pending','active','completed','returned') AND attempt > 0),
	CONSTRAINT fk_step_executions_instance_id_execution_instances FOREIGN KEY(instance_id) REFERENCES execution_instances (id),
	CONSTRAINT fk_step_executions_assignee_id_users FOREIGN KEY(assignee_id) REFERENCES users (id)
)
    """)
    op.execute("CREATE INDEX ix_step_executions_instance_id ON step_executions (instance_id)")
    op.execute("""
CREATE TABLE step_results (
	id UUID NOT NULL,
	step_execution_id UUID NOT NULL,
	values JSONB NOT NULL,
	comment VARCHAR(20000) NOT NULL,
	attachments JSONB NOT NULL,
	result_document JSONB,
	actor_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_step_results PRIMARY KEY (id),
	CONSTRAINT fk_step_results_step_execution_id_step_executions FOREIGN KEY(step_execution_id) REFERENCES step_executions (id),
	CONSTRAINT fk_step_results_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id)
)
    """)
    op.execute("CREATE INDEX ix_step_results_step_execution_id ON step_results (step_execution_id)")
    op.execute("""
CREATE TABLE execution_checklist (
	id UUID NOT NULL,
	step_execution_id UUID NOT NULL,
	ordinal INTEGER NOT NULL,
	label TEXT NOT NULL,
	kind VARCHAR(20) NOT NULL,
	required BOOLEAN NOT NULL,
	checked BOOLEAN NOT NULL,
	CONSTRAINT pk_execution_checklist PRIMARY KEY (id),
	CONSTRAINT fk_execution_checklist_step_execution_id_step_executions FOREIGN KEY(step_execution_id) REFERENCES step_executions (id)
)
    """)
    op.execute(
        "CREATE INDEX ix_execution_checklist_step_execution_id ON execution_checklist (step_execution_id)"
    )
    op.execute("""
CREATE TABLE execution_events (
	id UUID NOT NULL,
	instance_id UUID NOT NULL,
	step_execution_id UUID,
	action VARCHAR(30) NOT NULL,
	actor_id UUID NOT NULL,
	data JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_execution_events PRIMARY KEY (id),
	CONSTRAINT fk_execution_events_instance_id_execution_instances FOREIGN KEY(instance_id) REFERENCES execution_instances (id),
	CONSTRAINT fk_execution_events_step_execution_id_step_executions FOREIGN KEY(step_execution_id) REFERENCES step_executions (id),
	CONSTRAINT fk_execution_events_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id)
)
    """)
    op.execute("CREATE INDEX ix_execution_events_instance_id ON execution_events (instance_id)")

    op.execute("""
      CREATE FUNCTION protect_execution_instance() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF OLD.state IN ('completed','cancelled') OR TG_OP = 'DELETE' OR
          (to_jsonb(NEW) - ARRAY['state','revision','assignee_id','current_execution_id','path','deadline_at','completed_at'])
          IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['state','revision','assignee_id','current_execution_id','path','deadline_at','completed_at'])
        THEN RAISE EXCEPTION 'Execution snapshot or terminal instance is immutable'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER execution_instance_immutable BEFORE UPDATE OR DELETE ON execution_instances
      FOR EACH ROW EXECUTE FUNCTION protect_execution_instance();
      CREATE FUNCTION protect_step_execution() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP = 'INSERT' THEN
          IF NOT EXISTS (SELECT 1 FROM execution_instances WHERE id=NEW.instance_id AND state IN ('pending','active'))
          THEN RAISE EXCEPTION 'Terminal execution is immutable'; END IF;
          RETURN NEW;
        END IF;
        IF EXISTS (SELECT 1 FROM execution_instances WHERE id=OLD.instance_id AND state IN ('completed','cancelled'))
        THEN RAISE EXCEPTION 'Terminal execution is immutable'; END IF;
        IF OLD.state IN ('completed','returned') OR TG_OP = 'DELETE' OR
          (to_jsonb(NEW) - ARRAY['state','assignee_id','result_id','started_at','completed_at','deadline_at'])
          IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['state','assignee_id','result_id','started_at','completed_at','deadline_at'])
        THEN RAISE EXCEPTION 'Step identity or completed attempt is immutable'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER step_execution_immutable BEFORE INSERT OR UPDATE OR DELETE ON step_executions
      FOR EACH ROW EXECUTE FUNCTION protect_step_execution();
      CREATE FUNCTION protect_execution_fact() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'Execution fact is immutable'; END $$;
      CREATE TRIGGER step_result_immutable BEFORE UPDATE OR DELETE ON step_results
      FOR EACH ROW EXECUTE FUNCTION protect_execution_fact();
      CREATE FUNCTION protect_step_result_insert() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NOT EXISTS (SELECT 1 FROM step_executions s JOIN execution_instances i ON i.id=s.instance_id
          WHERE s.id=NEW.step_execution_id AND s.state='active' AND i.state='active' AND i.current_execution_id=s.id)
        THEN RAISE EXCEPTION 'Completed result fact is immutable'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER step_result_insert BEFORE INSERT ON step_results
      FOR EACH ROW EXECUTE FUNCTION protect_step_result_insert();
      CREATE TRIGGER execution_event_immutable BEFORE UPDATE OR DELETE ON execution_events
      FOR EACH ROW EXECUTE FUNCTION protect_execution_fact();
      CREATE FUNCTION protect_execution_checklist() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP = 'INSERT' THEN
          IF NOT EXISTS (SELECT 1 FROM step_executions s JOIN execution_instances i ON i.id=s.instance_id
            WHERE s.id=NEW.step_execution_id AND s.state='pending' AND i.state IN ('pending','active') AND i.current_execution_id=s.id)
          THEN RAISE EXCEPTION 'Completed checklist fact is immutable'; END IF;
          RETURN NEW;
        END IF;
        IF TG_OP = 'DELETE' OR (to_jsonb(NEW) - 'checked') IS DISTINCT FROM (to_jsonb(OLD) - 'checked')
          OR NOT EXISTS (SELECT 1 FROM step_executions s JOIN execution_instances i ON i.id=s.instance_id
            WHERE s.id=OLD.step_execution_id AND s.state='active' AND i.state='active' AND i.current_execution_id=s.id)
        THEN RAISE EXCEPTION 'Checklist identity or completed fact is immutable'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER execution_checklist_immutable BEFORE INSERT OR UPDATE OR DELETE ON execution_checklist
      FOR EACH ROW EXECUTE FUNCTION protect_execution_checklist();
    """)


def downgrade():
    op.drop_table("execution_events")
    op.drop_table("execution_checklist")
    op.drop_table("step_results")
    op.drop_table("step_executions")
    op.drop_table("execution_instances")
    op.execute(
        "DROP FUNCTION protect_execution_checklist(); DROP FUNCTION protect_step_result_insert(); DROP FUNCTION protect_execution_fact(); DROP FUNCTION protect_step_execution(); DROP FUNCTION protect_execution_instance()"
    )
