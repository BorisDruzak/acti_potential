from tests.conftest import login_admin


def test_router_defaults_and_openapi(client, factory):
    @client.app.state.protected_router.get("/default-protection-test")
    def new_route():
        return {"ok": True}

    client.app.include_router(client.app.state.protected_router, prefix="/test")
    assert client.get("/test/default-protection-test").status_code == 401
    assert client.get("/api/v1/openapi.json").status_code == 401
    assert login_admin(client, factory).status_code == 200
    schema = client.get("/api/v1/openapi.json").json()
    assert "APIKeyCookie" in schema["components"]["securitySchemes"]
