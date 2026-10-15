from sqlalchemy import select

from app.api.dependencies import COOKIE_NAME
from app.identity.models import AuthSession, LocalCredential, User
from tests.conftest import login_admin

TEMPORARY = "synthetic-temporary-password-only"
CHANGED = "synthetic-replacement-password-only"


def admin_headers(client, factory):
    assert login_admin(client, factory).status_code == 200
    return headers(client)


def headers(client):
    me = client.get("/api/v1/users/me")
    assert me.status_code == 200
    return {"Origin": "https://testserver", "X-CSRF-Token": me.json()["csrf_token"]}


def create_user(client, security, **overrides):
    return client.post(
        "/api/v1/admin/users",
        json={
            "login": " Editor ",
            "display_name": "Synthetic editor",
            "password": TEMPORARY,
            "roles": ["editor"],
            **overrides,
        },
        headers=security,
    )


def login(client, name="editor", password=TEMPORARY):
    return client.post(
        "/api/v1/auth/login",
        json={"login": name, "password": password},
        headers={"Origin": "https://testserver"},
    )


def test_admin_creates_compatible_accounts_without_credential_disclosure(client, factory):
    security = admin_headers(client, factory)
    created = create_user(client, security)
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["login"] == "editor"
    assert row["roles"] == ["editor"]
    assert row["must_change_password"] is True
    assert TEMPORARY not in created.text and "password_hash" not in created.text
    listed = client.get("/api/v1/admin/users")
    assert len(listed.json()) == 2
    assert TEMPORARY not in listed.text and "password_hash" not in listed.text
    assert create_user(client, security).status_code == 409
    assert create_user(client, security, login="bad", roles=["unknown"]).status_code == 422
    assert create_user(client, security, login="bad", password="short").status_code == 422
    assert create_user(client, security, login="compatible", roles=["user"]).status_code == 201


def test_temporary_password_forces_change_and_rotates_session(client, factory):
    security = admin_headers(client, factory)
    assert create_user(client, security, roles=["admin"]).status_code == 201
    assert login(client).status_code == 200
    me = client.get("/api/v1/users/me")
    assert me.json()["must_change_password"] is True
    assert client.get("/api/v1/admin/platform").status_code == 403
    previous_cookie = client.cookies.get(COOKIE_NAME)
    assert (
        client.post(
            "/api/v1/users/me/password",
            json={"current_password": "wrong", "new_password": CHANGED},
            headers=headers(client),
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/users/me/password",
            json={"current_password": TEMPORARY, "new_password": TEMPORARY},
            headers=headers(client),
        ).status_code
        == 422
    )
    changed = client.post(
        "/api/v1/users/me/password",
        json={"current_password": TEMPORARY, "new_password": CHANGED},
        headers=headers(client),
    )
    assert changed.status_code == 200, changed.text
    assert client.cookies.get(COOKIE_NAME) != previous_cookie
    assert client.get("/api/v1/users/me").json()["must_change_password"] is False
    assert client.get("/api/v1/admin/platform").status_code == 200
    assert login(client, password=TEMPORARY).status_code == 401
    assert login(client, password=CHANGED).status_code == 200


def test_block_and_password_reset_revoke_existing_sessions(client, factory):
    from uuid import UUID

    security = admin_headers(client, factory)
    created = create_user(client, security)
    assert created.status_code == 201, created.text
    uid = UUID(created.json()["id"])
    assert login(client).status_code == 200
    user_cookie = client.cookies.get(COOKIE_NAME)
    assert login(client, name="admin", password="synthetic-password-for-tests").status_code == 200
    reset = client.post(
        f"/api/v1/admin/users/{uid}/password", json={"password": CHANGED}, headers=headers(client)
    )
    assert reset.status_code == 204
    with factory() as db:
        assert db.get(LocalCredential, uid).must_change_password is True
        assert all(
            s.revoked_at is not None
            for s in db.scalars(select(AuthSession).where(AuthSession.user_id == uid))
        )
    client.cookies.set(COOKIE_NAME, user_cookie, domain="testserver.local", path="/")
    assert client.get("/api/v1/users/me").status_code == 401
    assert login(client, password=CHANGED).status_code == 200
    assert login(client, name="admin", password="synthetic-password-for-tests").status_code == 200
    assert (
        client.patch(
            f"/api/v1/admin/users/{uid}", json={"active": False}, headers=headers(client)
        ).status_code
        == 200
    )
    assert login(client, password=CHANGED).status_code == 401
    with factory() as db:
        assert db.get(User, uid).active is False


def test_last_active_admin_cannot_be_disabled_or_demoted(client, factory):
    security = admin_headers(client, factory)
    uid = client.get("/api/v1/users/me").json()["id"]
    assert (
        client.patch(
            f"/api/v1/admin/users/{uid}", json={"active": False}, headers=security
        ).status_code
        == 409
    )
    assert (
        client.patch(
            f"/api/v1/admin/users/{uid}", json={"roles": ["viewer"]}, headers=security
        ).status_code
        == 409
    )
    assert create_user(client, security, login="second-admin", roles=["admin"]).status_code == 201
    assert (
        client.patch(
            f"/api/v1/admin/users/{uid}", json={"roles": ["user"]}, headers=security
        ).status_code
        == 200
    )
    assert client.get("/api/v1/admin/users").status_code == 403


def test_ordinary_user_cannot_administer_users_or_read_audit(client, factory):
    security = admin_headers(client, factory)
    assert create_user(client, security).status_code == 201
    assert login(client).status_code == 200
    assert client.post(
        "/api/v1/users/me/password",
        json={"current_password": TEMPORARY, "new_password": CHANGED},
        headers=headers(client),
    ).status_code == 200
    assert client.get("/api/v1/users/me").json()["must_change_password"] is False
    assert client.get("/api/v1/admin/users").status_code == 403
    assert client.get("/api/v1/admin/audit").status_code == 403
    assert (
        create_user(client, headers(client), login="intruder", roles=["admin"]).status_code == 403
    )


def test_audit_page_exposes_only_metadata(client, factory):
    security = admin_headers(client, factory)
    assert create_user(client, security).status_code == 201
    response = client.get("/api/v1/admin/audit?page=1&page_size=2")
    assert response.status_code == 200
    result = response.json()
    assert result["total"] >= 2 and len(result["items"]) == 2
    assert TEMPORARY not in response.text
    assert all("request_source" not in item and "password" not in item for item in result["items"])


def test_registry_access_replacement_validates_and_revokes_omitted_grants(client, factory):
    from uuid import UUID, uuid4

    from app.registry.models import Registry, RegistryAccess

    security = admin_headers(client, factory)
    created = create_user(client, security)
    assert created.status_code == 201
    uid = created.json()["id"]
    with factory.begin() as db:
        registry = Registry(code="synthetic-access", name="Synthetic registry")
        db.add(registry)
        db.flush()
        rid = registry.id
    endpoint = f"/api/v1/admin/access/{rid}"
    grant = {"entries": [{"user_id": uid, "permissions": ["read", "download"]}]}
    response = client.put(endpoint, json=grant, headers=security)
    assert response.status_code == 200, response.text
    assert response.json()["entries"][0]["permissions"] == ["download", "read"]
    assert client.get(endpoint).json() == response.json()
    invalid = {"entries": [{"user_id": uid, "permissions": ["root"]}]}
    assert client.put(endpoint, json=invalid, headers=security).status_code == 422
    missing = {"entries": [{"user_id": str(uuid4()), "permissions": ["read"]}]}
    assert client.put(endpoint, json=missing, headers=security).status_code == 422
    assert client.get(endpoint).json() == response.json()
    assert client.put(endpoint, json={"entries": []}, headers=security).status_code == 200
    assert client.get(endpoint).json() == {"entries": []}
    with factory() as db:
        assert db.get(RegistryAccess, (rid, UUID(uid))).permissions == []
    assert login(client).status_code == 200
    assert client.get(endpoint).status_code == 403
    assert client.put(endpoint, json=grant, headers=headers(client)).status_code == 403


def test_concurrent_admin_demotion_preserves_one_active_administrator(factory):
    from concurrent.futures import ThreadPoolExecutor

    from fastapi import HTTPException

    from app.identity.account_service import create_account, update_account
    from app.identity.bootstrap import bootstrap_admin
    from app.identity.models import Role, UserRole

    with factory.begin() as db:
        first = bootstrap_admin(db, "first", "First administrator", TEMPORARY)
        second = create_account(
            db,
            login="second",
            display_name="Second administrator",
            password=TEMPORARY,
            roles=["admin"],
        ).id

    def demote(uid):
        try:
            with factory.begin() as db:
                update_account(db, uid, {"roles": ["viewer"]})
            return "demoted"
        except HTTPException as exc:
            assert exc.status_code == 409
            return "preserved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(demote, [first, second]))
    assert sorted(outcomes) == ["demoted", "preserved"]
    with factory() as db:
        ids = list(
            db.scalars(
                select(User.id)
                .join(UserRole)
                .join(Role)
                .where(User.active.is_(True), Role.code == "admin")
            )
        )
        assert len(ids) == 1
