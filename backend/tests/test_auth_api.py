from sqlalchemy import delete

from app.identity.models import UserRole
from tests.conftest import login_admin


def test_login_me_logout_csrf_and_cookie(client, factory):
    assert client.get("/api/v1/users/me").status_code == 401
    response = login_admin(client, factory)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert all(flag in cookie for flag in ["httponly", "secure", "samesite=lax", "path=/"])
    assert "domain=" not in cookie
    me = client.get("/api/v1/users/me")
    assert me.status_code == 200
    assert me.json()["roles"] == ["admin"]
    assert client.get("/api/v1/admin/platform").status_code == 200
    assert (
        client.post("/api/v1/auth/logout", headers={"Origin": "https://testserver"}).status_code
        == 403
    )
    headers = {"Origin": "https://testserver", "X-CSRF-Token": me.json()["csrf_token"]}
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/v1/users/me").status_code == 401


def test_role_change_applies_to_current_session(client, factory):
    assert login_admin(client, factory).status_code == 200
    with factory.begin() as db:
        db.execute(delete(UserRole))
    assert client.get("/api/v1/admin/platform").status_code == 403


def test_bad_origin_and_validation_redacts_password(client):
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"login": "a", "password": "x"},
            headers={"Origin": "https://evil.invalid"},
        ).status_code
        == 403
    )
    response = client.post(
        "/api/v1/auth/login",
        json={"login": "a", "password": "x" * 1025},
        headers={"Origin": "https://testserver"},
    )
    assert response.status_code == 422
    assert "x" * 1025 not in response.text


def test_login_limiter_and_dependency_failure(client, monkeypatch):
    for _ in range(10):
        assert (
            client.post(
                "/api/v1/auth/login",
                json={"login": "missing", "password": "wrong"},
                headers={"Origin": "https://testserver"},
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"login": "missing", "password": "wrong"},
            headers={"Origin": "https://testserver", "X-Real-IP": "another-client"},
        ).status_code
        == 429
    )

    def unavailable(*args, **kwargs):
        from redis import ConnectionError

        raise ConnectionError("dependency unavailable")

    monkeypatch.setattr(client.app.state.redis, "eval", unavailable)
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"login": "missing", "password": "wrong"},
            headers={"Origin": "https://testserver"},
        ).status_code
        == 503
    )
