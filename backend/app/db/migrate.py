from pathlib import Path
from threading import Lock

from alembic import command
from alembic.config import Config
from sqlalchemy import text

_process_lock = Lock()  # Alembic EnvironmentContext uses process-global state.


def upgrade_database(engine):
    with _process_lock, engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(731001)"))
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        from sqlalchemy.orm import Session

        from app.registry.builtin import seed_builtin

        with Session(bind=connection, join_transaction_mode="create_savepoint") as seed_session:
            with seed_session.begin():
                seed_builtin(seed_session)
        if connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='acti_app')")
        ):
            connection.execute(text("GRANT USAGE ON SCHEMA public TO acti_app"))
            connection.execute(
                text("GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO acti_app")
            )
            connection.execute(
                text(
                    "REVOKE UPDATE ON audit_events, number_operations, number_requests, number_entries, document_blocks, relation_events, document_relations FROM acti_app"
                )
            )
            connection.execute(
                text("GRANT UPDATE (state, document_id) ON number_entries TO acti_app")
            )
            connection.execute(text("GRANT UPDATE (state, decided_by, decided_at, effective_date, reason) ON document_relations TO acti_app"))
            connection.execute(
                text(
                    "GRANT DELETE ON user_roles, registry_access, document_acl, number_grants, content_metadata_fragments, process_steps, process_transitions, process_step_sources, process_step_templates TO acti_app"
                )
            )


if __name__ == "__main__":
    from app.core.config import Settings
    from app.db.session import build_engine

    upgrade_database(build_engine(Settings()))
