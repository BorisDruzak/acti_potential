import base64
import hashlib
import json
import time
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from app.identity import oidc
from app.identity.models import ExternalIdentity, Role, UserRole
from app.identity.service import AuthenticationError
from app.identity.sessions import resolve_actor
from tests.conftest import login_admin


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def configured(client):
    settings = client.app.state.settings
    settings.oidc_enabled = True
    settings.oidc_issuer = "https://idp.example.test/realms/acti"
    settings.oidc_client_id = "acti-browser"
    from pydantic import SecretStr
    settings.oidc_client_secret = SecretStr("synthetic-client-secret")
    return settings


def claims(settings, nonce="nonce"):
    now = int(time.time())
    return {"iss": settings.oidc_issuer, "sub": "subject-1", "aud": settings.oidc_client_id,
            "iat": now, "exp": now + 60, "nonce": nonce}


def jwks(signing_key):
    key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    key.update(kid="key-1", use="sig", alg="RS256")
    return {"keys": [key]}


def signed(signing_key, payload):
    return jwt.encode(payload, signing_key, algorithm="RS256", headers={"kid": "key-1"})


@pytest.mark.parametrize("change", [
    {"iss": "https://other.example.test"}, {"aud": "another-client"},
    {"azp": "another-client"}, {"nonce": "wrong"}, {"sub": ""},
    {"exp": 1}, {"iat": 1}, {"iat": "1"}, {"exp": "9999999999"},
    {"aud": ["acti-browser", "another-client"]}, {"sub": "x" * 256},
])
def test_signed_invalid_claims_rejected(client, signing_key, change):
    settings = configured(client)
    payload = claims(settings) | change
    with pytest.raises(oidc.OIDCError):
        oidc.validate_token(settings, signed(signing_key, payload), jwks(signing_key), "nonce")


def test_signature_algorithm_and_key_selection_fail_closed(client, signing_key):
    settings = configured(client)
    payload = claims(settings)
    assert oidc.validate_token(settings, signed(signing_key, payload), jwks(signing_key), "nonce") == "subject-1"
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    invalid = [signed(attacker, payload), jwt.encode(payload, "synthetic-secret-at-least-32-bytes", algorithm="HS256"),
               jwt.encode(payload, None, algorithm="none")]
    for token in invalid:
        with pytest.raises(oidc.OIDCError):
            oidc.validate_token(settings, token, jwks(signing_key), "nonce")
    with pytest.raises(oidc.OIDCError):
        oidc.validate_token(settings, signed(signing_key, payload), {"keys": jwks(signing_key)["keys"] * 2}, "nonce")


def fake_provider(monkeypatch, settings, signing_key, *, claim_changes=None):
    captured = {}
    issuer = settings.oidc_issuer
    metadata = {
        "issuer": issuer, "authorization_endpoint": issuer + "/authorize",
        "token_endpoint": issuer + "/token", "jwks_uri": issuer + "/certs",
        "response_types_supported": ["code"], "id_token_signing_alg_values_supported": ["RS256"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
    }

    def fetch(current, method, url, **kwargs):
        if url.endswith("openid-configuration"):
            return metadata
        if url.endswith("/certs"):
            return jwks(signing_key)
        assert method == "POST" and url.endswith("/token")
        captured["exchange"] = kwargs["data"]
        return {"id_token": signed(signing_key, claims(settings, captured["nonce"]) | (claim_changes or {}))}

    monkeypatch.setattr(oidc, "fetch_json", fetch)
    return captured


def start_flow(client, captured):
    response = client.post("/api/v1/auth/oidc/start", headers={"Origin": "https://testserver"})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert all(flag in cookie for flag in ("httponly", "secure", "samesite=lax", "max-age=300"))
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    captured["nonce"] = query["nonce"][0]
    return query


def admin_link(client, factory, settings, subject="subject-1"):
    assert login_admin(client, factory).status_code == 200
    me = client.get("/api/v1/users/me").json()
    headers = {"Origin": "https://testserver", "X-CSRF-Token": me["csrf_token"]}
    response = client.post(f'/api/v1/admin/users/{me["id"]}/external-identities',
                           headers=headers, json={"subject": subject})
    assert response.status_code == 200, response.text
    return me, headers, response.json()


def test_disabled_origin_and_provider_endpoints(client):
    assert client.get("/api/v1/auth/oidc/config").json()["enabled"] is False
    client.app.state.settings.oidc_enabled = True
    assert client.get("/api/v1/auth/oidc/config").json()["enabled"] is False
    assert client.post("/api/v1/auth/oidc/start", headers={"Origin": "https://evil.invalid"}).status_code == 403
    assert client.post("/api/v1/auth/oidc/start", headers={"Origin": "https://testserver"}).status_code == 503
    settings = configured(client)
    for url in ("http://idp.example.test/token", "https://evil.invalid/token",
                "https://idp.example.test/token?x=1", "https://user@idp.example.test/token"):
        with pytest.raises(oidc.OIDCError):
            oidc.endpoint(settings, url)


def test_missing_ca_is_safe_provider_error(client, tmp_path):
    settings = configured(client)
    settings.oidc_ca_file = str(tmp_path / "missing-ca.pem")
    with pytest.raises(oidc.OIDCError):
        oidc.fetch_json(settings, "GET", settings.oidc_issuer + "/metadata")


def test_code_exchange_preserves_uuid_roles_and_replay_rejected(client, factory, signing_key, monkeypatch):
    settings = configured(client)
    me, headers, identity = admin_link(client, factory, settings)
    local_token = client.cookies.get("acti_session")
    captured = fake_provider(monkeypatch, settings, signing_key)
    query = start_flow(client, captured)
    response = client.get("/api/v1/auth/oidc/callback", params={"state": query["state"][0], "code": "one-time"}, follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"] == "https://testserver/"
    current = client.get("/api/v1/users/me").json()
    assert current["id"] == me["id"] and current["roles"] == me["roles"]
    verifier = captured["exchange"]["code_verifier"]
    assert base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode() == query["code_challenge"][0]
    repeated = client.get("/api/v1/auth/oidc/callback", params={"state": query["state"][0], "code": "one-time"}, follow_redirects=False)
    assert repeated.headers["location"].endswith("auth_error=oidc_failed")
    oidc_token = client.cookies.get("acti_session")
    current_headers = {"Origin": "https://testserver", "X-CSRF-Token": current["csrf_token"]}
    assert client.delete(f'/api/v1/admin/users/{me["id"]}/external-identities/{identity["id"]}', headers=current_headers).status_code == 204
    assert client.get("/api/v1/users/me").status_code == 401
    with factory.begin() as db:
        assert str(resolve_actor(db, local_token, datetime.now(UTC)).id) == me["id"]
        with pytest.raises(AuthenticationError):
            resolve_actor(db, oidc_token, datetime.now(UTC))


@pytest.mark.parametrize("failure", ["binding", "nonce", "unknown", "inactive", "expired", "issuer_change"])
def test_flow_denies_unbound_unlinked_disabled_and_expired(client, factory, signing_key, monkeypatch, failure):
    settings = configured(client)
    me, headers, identity = admin_link(client, factory, settings)
    captured = fake_provider(monkeypatch, settings, signing_key,
                             claim_changes={"nonce": "wrong"} if failure == "nonce" else {})
    query = start_flow(client, captured)
    redis = client.app.state.redis
    if failure == "binding":
        client.cookies.delete(oidc.BINDING_COOKIE)
    elif failure in {"unknown", "inactive"}:
        with factory.begin() as db:
            row = db.get(ExternalIdentity, identity["id"])
            if failure == "unknown":
                row.subject = "another-subject"
            else:
                row.active = False
    elif failure == "expired":
        redis.delete("oidc:" + oidc.digest(query["state"][0]))
    elif failure == "issuer_change":
        settings.oidc_issuer += "-changed"
    response = client.get("/api/v1/auth/oidc/callback", params={"state": query["state"][0], "code": "one-time"}, follow_redirects=False)
    code = "identity_not_linked" if failure in {"unknown", "inactive"} else "oidc_failed"
    assert response.headers["location"].endswith("auth_error=" + code)
    assert redis.get("oidc:" + oidc.digest(query["state"][0])) is None


def test_admin_link_never_reassigns_subject_or_grants_roles(client, factory):
    from app.identity.models import User
    settings = configured(client)
    me, headers, identity = admin_link(client, factory, settings)
    with factory.begin() as db:
        viewer = User(display_name="Viewer")
        db.add(viewer)
        db.flush()
        role = db.scalar(select(Role).where(Role.code == "viewer"))
        db.add(UserRole(user_id=viewer.id, role_id=role.id))
        viewer_id = str(viewer.id)
    assert client.post(f'/api/v1/admin/users/{viewer_id}/external-identities', headers=headers,
                       json={"subject": "subject-1"}).status_code == 409
    assert client.delete(f'/api/v1/admin/users/{me["id"]}/external-identities/{identity["id"]}', headers=headers).status_code == 204
    assert client.post(f'/api/v1/admin/users/{viewer_id}/external-identities', headers=headers,
                       json={"subject": "subject-1"}).status_code == 409
    assert client.get("/api/v1/users/me").json()["roles"] == ["admin"]


@pytest.mark.parametrize("failure", ["redirect", "oversized", "array", "server_error"])
def test_provider_http_is_bounded_and_does_not_follow_redirects(client, monkeypatch, failure):
    import httpx
    settings = configured(client)
    requests = []
    original = httpx.Client

    def handler(request):
        requests.append(request)
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "https://evil.invalid/"})
        if failure == "oversized":
            return httpx.Response(200, json={"value": "x" * (1024 * 1024)})
        if failure == "array":
            return httpx.Response(200, json=[])
        return httpx.Response(500, text="sensitive-provider-detail")

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs,
    ))
    with pytest.raises(oidc.OIDCError) as error:
        oidc.fetch_json(settings, "GET", settings.oidc_issuer + "/metadata")
    assert len(requests) == 1
    assert "sensitive-provider-detail" not in str(error.value)


def test_ordinary_viewer_cannot_link_or_unlink(client, factory):
    from sqlalchemy import delete
    settings = configured(client)
    me, headers, identity = admin_link(client, factory, settings)
    with factory.begin() as db:
        db.execute(delete(UserRole))
        viewer = db.scalar(select(Role).where(Role.code == "viewer"))
        from uuid import UUID
        db.add(UserRole(user_id=UUID(me["id"]), role_id=viewer.id))
    path = f'/api/v1/admin/users/{me["id"]}/external-identities'
    assert client.get(path).status_code == 403
    assert client.post(path, json={"subject": "viewer-takeover"}, headers=headers).status_code == 403
    assert client.delete(path + "/" + identity["id"], headers=headers).status_code == 403


@pytest.mark.parametrize("kind", ["inactive", "service", "roleless", "invalid_role"])
def test_link_rejects_ineligible_accounts(client, factory, kind):
    from uuid import UUID

    from sqlalchemy import delete

    from app.identity.models import User
    from app.service_api.models import ServiceAccount
    settings = configured(client)
    me, headers, identity = admin_link(client, factory, settings)
    with factory.begin() as db:
        target = User(display_name="Ineligible", active=kind != "inactive")
        db.add(target)
        db.flush()
        if kind != "roleless":
            if kind == "invalid_role":
                role = db.scalar(select(Role).where(Role.code == "synthetic-untrusted-role"))
                if role is None:
                    role = Role(code="synthetic-untrusted-role")
                    db.add(role)
                    db.flush()
                invalid_role_id = role.id
            else:
                role = db.scalar(select(Role).where(Role.code == "viewer"))
            db.add(UserRole(user_id=target.id, role_id=role.id))
        if kind == "service":
            db.add(ServiceAccount(user_id=target.id, name="Noninteractive", registry_ids=[],
                                  operations=[], created_by=UUID(me["id"])))
        target_id = str(target.id)
    try:
        result = client.post(f'/api/v1/admin/users/{target_id}/external-identities',
                             json={"subject": "ineligible-subject"}, headers=headers)
        assert result.status_code == 409
    finally:
        if kind == "invalid_role":
            with factory.begin() as db:
                db.execute(delete(UserRole).where(UserRole.role_id == invalid_role_id))
                db.execute(delete(Role).where(Role.id == invalid_role_id))


@pytest.mark.parametrize("active", [True, False])
def test_migration_cannot_discard_existing_identity(client, factory, active):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    settings = configured(client)
    me, headers, identity = admin_link(client, factory, settings)
    with factory.begin() as db:
        db.get(ExternalIdentity, identity["id"]).active = active
    engine = factory.kw["bind"]
    with pytest.raises(DBAPIError):
        with engine.begin() as connection:
            config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
            config.attributes["connection"] = connection
            command.downgrade(config, "0010_service_api")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0019_number_year_type"
        assert connection.scalar(text("SELECT count(*) FROM external_identities")) == 1
