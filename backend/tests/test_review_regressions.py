import os
import time

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from tests.conftest import login_admin


def test_rejected_login_releases_connection_before_audit(client, factory):
    assert login_admin(client, factory).status_code == 200
    engine = create_engine(
        os.environ["ACTI_DATABASE_URL"], pool_size=1, max_overflow=0, pool_timeout=0.2
    )
    client.app.state.factory = sessionmaker(engine)
    try:
        response = client.post(
            "/api/v1/auth/login",
            json={"login": "admin", "password": "wrong"},
            headers={"Origin": "https://testserver"},
        )
        assert response.status_code == 401
    finally:
        engine.dispose()


def test_readiness_bounds_database_lock(client, factory):
    with factory.kw["bind"].begin() as conn:
        conn.execute(text("LOCK TABLE alembic_version IN ACCESS EXCLUSIVE MODE"))
        started = time.monotonic()
        response = client.get("/api/v1/health/ready")
        assert response.status_code == 503
        assert time.monotonic() - started < 3


def test_readiness_bounds_pool_checkout(client):
    with client.app.state.readiness_engine.connect():
        started = time.monotonic()
        assert client.get("/api/v1/health/ready").status_code == 503
        assert time.monotonic() - started < 3


def test_openapi_describes_real_success_and_error_envelopes(client, factory):
    assert login_admin(client, factory).status_code == 200
    schema = client.get("/api/v1/openapi.json").json()
    login = schema["paths"]["/api/v1/auth/login"]["post"]["responses"]
    assert {"200", "401", "403", "422", "429", "500", "503"} <= set(login)
    assert login["422"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorEnvelope")
    me = schema["paths"]["/api/v1/users/me"]["get"]["responses"]["200"]
    assert me["content"]["application/json"]["schema"]["$ref"].endswith("/CurrentUserResponse")
