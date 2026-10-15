from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def build_engine(settings):
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3},
        hide_parameters=True,
    )


@contextmanager
def session_scope(factory):
    with factory.begin() as session:
        yield session


def validate_test_database(url: str) -> None:
    if not (make_url(url).database or "").endswith("_test"):
        raise ValueError("Refusing destructive tests outside a test database")
