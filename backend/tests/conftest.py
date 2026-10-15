import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db.migrate import upgrade_database
from app.db.session import validate_test_database


@pytest.fixture
def factory():
    url = os.environ["ACTI_DATABASE_URL"]
    validate_test_database(url)
    engine = create_engine(url, hide_parameters=True)
    upgrade_database(engine)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE users, outbox_events RESTART IDENTITY CASCADE"))
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def client(factory, tmp_path):
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    settings = Settings(
        database_url=os.environ["ACTI_DATABASE_URL"],
        redis_url=os.environ["ACTI_REDIS_URL"],
        session_secret="synthetic-key-for-test-only",
        storage_root=str(tmp_path),
        origin="https://testserver",
    )
    app = create_app(settings)
    app.state.factory = factory
    app.state.redis.flushdb()
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as http:
        yield http
    app.state.engine.dispose()


def login_admin(client, factory):
    from app.identity.bootstrap import bootstrap_admin

    with factory.begin() as db:
        bootstrap_admin(db, "admin", "Administrator", "synthetic-password-for-tests")
    return client.post(
        "/api/v1/auth/login",
        json={"login": "admin", "password": "synthetic-password-for-tests"},
        headers={"Origin": "https://testserver"},
    )
