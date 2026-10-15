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


if __name__ == "__main__":
    from app.core.config import Settings
    from app.db.session import build_engine

    upgrade_database(build_engine(Settings()))
