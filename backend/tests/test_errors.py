def test_error_envelope_and_request_id(client):
    for path, status in [("/api/v1/users/me", 401), ("/missing", 404)]:
        result = client.get(path, headers={"X-Request-ID": "test-correlation"})
        assert result.status_code == status
        assert result.json()["request_id"] == result.headers["x-request-id"] == "test-correlation"
        assert set(result.json()["error"]) == {"code", "message", "details"}

    @client.app.get("/failure-test")
    def fail():
        raise RuntimeError("private failure data")

    result = client.get("/failure-test")
    assert result.status_code == 500
    assert "private failure data" not in result.text
    assert result.json()["request_id"] == result.headers["x-request-id"]
