def test_live_and_ready_dependencies(client, monkeypatch):
    assert client.get("/api/v1/health/live").status_code == 200
    assert client.get("/api/v1/health/ready").status_code == 200

    def fail():
        raise RuntimeError("private dependency details")

    monkeypatch.setattr(client.app.state.redis, "ping", fail)
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert "private dependency details" not in response.text
    assert client.get("/api/v1/health/live").status_code == 200


def test_ready_rejects_missing_storage(client):
    client.app.state.settings.storage_root += "/missing"
    assert client.get("/api/v1/health/ready").status_code == 503


def test_ready_rejects_schema_mismatch(client, factory):
    from sqlalchemy import text

    engine = factory.kw["bind"]
    with engine.begin() as conn:
        original_revision = conn.scalar(text("SELECT version_num FROM alembic_version"))
        conn.execute(text("UPDATE alembic_version SET version_num='unexpected_revision'"))
    try:
        assert client.get("/api/v1/health/ready").status_code == 503
        assert client.get("/api/v1/health/live").status_code == 200
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE alembic_version SET version_num=:revision"),
                {"revision": original_revision},
            )


def test_ready_rejects_unreachable_database(client):
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    original = client.app.state.readiness_engine
    unavailable = create_engine(
        make_url(client.app.state.settings.database_url.get_secret_value()).set(port=1),
        connect_args={"connect_timeout": 1},
        pool_timeout=0.5,
    )
    client.app.state.readiness_engine = unavailable
    try:
        assert client.get("/api/v1/health/ready").status_code == 503
        assert client.get("/api/v1/health/live").status_code == 200
    finally:
        unavailable.dispose()
        client.app.state.readiness_engine = original
