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
