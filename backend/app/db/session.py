from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def build_engine(settings, *, readiness: bool = False):
    connection_args = {"connect_timeout": 3}
    pool_args = {"pool_timeout": 2}
    if readiness:
        connection_args = {
            "connect_timeout": 1,
            "options": "-c statement_timeout=1000 -c lock_timeout=500",
        }
        pool_args = {"pool_timeout": 0.5, "pool_size": 1, "max_overflow": 0}
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args=connection_args,
        **pool_args,
        hide_parameters=True,
    )


@contextmanager
def session_scope(factory):
    with factory.begin() as session:
        yield session


def validate_test_database(url: str) -> None:
    if not (make_url(url).database or "").endswith("_test"):
        raise ValueError("Refusing destructive tests outside a test database")
