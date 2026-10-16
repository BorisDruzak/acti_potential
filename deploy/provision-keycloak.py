#!/usr/bin/env python3
"""Provision only ACTI's new IdP schema/realm; never modify Active Directory.

Run prepare before enabling the optional Compose profile; configure after the
provider has started behind ACTI's verified HTTPS endpoint. Generated credentials
remain in root-only state and the existing encrypted external backup.
"""

import argparse
import fcntl
import importlib.util
import json
import os
import secrets
import shlex
import ssl
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

ROOT = Path("/etc/acti-v3")
STATE = ROOT / "idp/provision.json"
ENV = ROOT / "runtime.env"
MARKER = "acti-managed-keycloak-v1"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("idp_admin_redirect_forbidden")


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def write_private(path, value):
    temporary = path.with_suffix(".partial")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def save_state(state):
    write_private(STATE, json.dumps(state, indent=2, sort_keys=True) + "\n")


def save_env(values):
    write_private(ENV, "".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items()))


def sql(statement):
    result = subprocess.run(
        ["docker", "exec", "-i", "acti-v3-postgres-1", "psql", "-X", "-U", "postgres",
         "-d", "acti", "-v", "ON_ERROR_STOP=1", "-At"],
        input=statement, capture_output=True, text=True, timeout=30,
    )
    require(result.returncode == 0, "idp_database_operation_failed")
    return result.stdout.strip()


def prepare(values, helper):
    directory = ROOT / "idp"
    if not directory.exists():
        directory.mkdir(mode=0o700)
    helper.protected(directory, directory=True)
    trust = directory / "trust"
    if not trust.exists():
        trust.mkdir(mode=0o755)
    require(trust.resolve(strict=True) == trust and trust.stat().st_uid == 0
            and not trust.stat().st_mode & 0o022, "unsafe_idp_trust_directory")
    if STATE.exists():
        state = json.loads(helper.protected(STATE).read_text())
        require(state.get("owner") == MARKER, "foreign_idp_state")
    else:
        require(sql("SELECT count(*) FROM pg_roles WHERE rolname='acti_keycloak';") == "0",
                "existing_idp_role_without_ownership")
        require(sql("SELECT count(*) FROM pg_namespace WHERE nspname='keycloak';") == "0",
                "existing_idp_schema_without_ownership")
        state = {
            "owner": MARKER, "origin": values["ACTI_ORIGIN"],
            "db_password": secrets.token_urlsafe(48),
            "bootstrap": {"login": "acti-idp-bootstrap", "password": secrets.token_urlsafe(48)},
            "administrator": {"login": "acti-idp-operator", "password": secrets.token_urlsafe(48)},
            "client_secret": secrets.token_urlsafe(48),
            "pilot_accounts": [
                {"role": role, "login": "acti-pilot-" + role,
                 "password": secrets.token_urlsafe(32)}
                for role in ("admin", "editor", "viewer", "unlinked")
            ],
        }
        save_state(state)
    require(state["origin"] == values["ACTI_ORIGIN"], "idp_origin_changed")
    role = sql("SELECT coalesce(shobj_description(oid,'pg_authid'),'') FROM pg_roles WHERE rolname='acti_keycloak';")
    if not role:
        require(sql("SELECT count(*) FROM pg_roles WHERE rolname='acti_keycloak';") == "0",
                "unowned_idp_role")
        sql("CREATE ROLE acti_keycloak LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
            "PASSWORD " + helper.literal(state["db_password"]) + "; COMMENT ON ROLE acti_keycloak IS "
            + helper.literal(MARKER) + ";")
    else:
        require(role == MARKER, "foreign_idp_role")
    schema = sql("SELECT coalesce(obj_description(oid,'pg_namespace'),'') FROM pg_namespace WHERE nspname='keycloak';")
    if not schema:
        require(sql("SELECT count(*) FROM pg_namespace WHERE nspname='keycloak';") == "0",
                "unowned_idp_schema")
        sql("CREATE SCHEMA keycloak AUTHORIZATION acti_keycloak; COMMENT ON SCHEMA keycloak IS "
            + helper.literal(MARKER) + "; REVOKE ALL ON SCHEMA keycloak FROM PUBLIC;")
    else:
        require(schema == MARKER, "foreign_idp_schema")
    require(sql("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='keycloak';")
            == "acti_keycloak", "foreign_idp_schema_owner")
    sql("GRANT CONNECT ON DATABASE acti TO acti_keycloak; "
        "ALTER ROLE acti_keycloak IN DATABASE acti SET search_path=keycloak;")
    privileges = json.loads(sql("SELECT json_build_object('role_safe',"
        "(SELECT NOT(rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls) "
        "FROM pg_roles WHERE rolname='acti_keycloak'),'app_denied',"
        "NOT has_schema_privilege('acti_app','keycloak','USAGE'),'owner_denied',"
        "NOT has_schema_privilege('acti_owner','keycloak','USAGE'),'business_denied',"
        "NOT has_table_privilege('acti_keycloak','public.users','SELECT,INSERT,UPDATE,DELETE'))"))
    require(all(privileges.values()), "idp_database_isolation_failed")
    values.update({"COMPOSE_PROFILES": "idp", "ACTI_IDP_DB_PASSWORD": state["db_password"]})
    if not state.get("configured"):
        values.update({"KC_BOOTSTRAP_ADMIN_USERNAME": state["bootstrap"]["login"],
                       "KC_BOOTSTRAP_ADMIN_PASSWORD": state["bootstrap"]["password"]})
    save_env(values)
    return {"status": "prepared", "isolation": privileges}


def configure(values, helper):
    state = json.loads(helper.protected(STATE).read_text())
    require(state["owner"] == MARKER and state["origin"] == values["ACTI_ORIGIN"], "foreign_idp_state")
    base = values["ACTI_ORIGIN"] + "/idp"
    context = ssl.create_default_context(cafile=str(ROOT / "tls/ca.crt"))
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())

    def request(method, path, body=None, token=None, form=False, expected=(200, 201, 204)):
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
            body = (urlencode(body) if form else json.dumps(body)).encode()
        req = Request(base + path, data=body, headers=headers, method=method)
        try:
            with opener.open(req, timeout=15) as response:
                require(response.status in expected, "idp_admin_unexpected_status")
                data = response.read(4 * 1024**2 + 1)
                require(len(data) <= 4 * 1024**2, "idp_admin_response_too_large")
                return json.loads(data) if data else None
        except HTTPError as error:
            if error.code == 404 and 404 in expected:
                return None
            raise RuntimeError("idp_admin_request_rejected") from None

    def token_for(account):
        return request("POST", "/realms/master/protocol/openid-connect/token", {
            "client_id": "admin-cli", "grant_type": "password", "username": account["login"],
            "password": account["password"],
        }, form=True)["access_token"]

    try:
        token = token_for(state["administrator"])
    except RuntimeError:
        token = token_for(state["bootstrap"])
        existing = request("GET", "/admin/realms/master/users?" + urlencode({
            "username": state["administrator"]["login"], "exact": "true"}), token=token)
        if not existing:
            request("POST", "/admin/realms/master/users", {
                "username": state["administrator"]["login"], "enabled": True,
                "firstName": "ACTI", "lastName": "Operator",
                "email": "acti-idp-operator@example.invalid", "emailVerified": True,
                "credentials": [{"type": "password", "value": state["administrator"]["password"],
                                 "temporary": False}],
            }, token=token)
            existing = request("GET", "/admin/realms/master/users?" + urlencode({
                "username": state["administrator"]["login"], "exact": "true"}), token=token)
        require(len(existing) == 1, "idp_operator_not_unique")
        role = request("GET", "/admin/realms/master/roles/admin", token=token)
        request("POST", "/admin/realms/master/users/" + existing[0]["id"] + "/role-mappings/realm", [role], token)
        token = token_for(state["administrator"])
    realm = request("GET", "/admin/realms/acti", token=token, expected=(200, 404))
    if realm is None:
        request("POST", "/admin/realms", {
            "realm": "acti", "enabled": True, "displayName": "ACTI — единый вход",
            "registrationAllowed": False, "resetPasswordAllowed": False,
            "loginWithEmailAllowed": False, "duplicateEmailsAllowed": False,
            "bruteForceProtected": True, "sslRequired": "all", "accessTokenLifespan": 120,
            "ssoSessionIdleTimeout": 1800, "ssoSessionMaxLifespan": 43200,
            "internationalizationEnabled": True, "supportedLocales": ["ru", "en"],
            "defaultLocale": "ru", "attributes": {"actiManaged": MARKER},
        }, token)
    else:
        require(realm.get("attributes", {}).get("actiManaged") == MARKER, "foreign_idp_realm")
    clients = request("GET", "/admin/realms/acti/clients?clientId=acti-web", token=token)
    if not clients:
        request("POST", "/admin/realms/acti/clients", {
            "clientId": "acti-web", "protocol": "openid-connect", "enabled": True,
            "publicClient": False, "secret": state["client_secret"],
            "standardFlowEnabled": True, "implicitFlowEnabled": False,
            "directAccessGrantsEnabled": False, "serviceAccountsEnabled": False,
            "redirectUris": [values["ACTI_ORIGIN"] + "/api/v1/auth/oidc/callback"],
            "webOrigins": [], "attributes": {"pkce.code.challenge.method": "S256", "actiManaged": MARKER},
        }, token)
        clients = request("GET", "/admin/realms/acti/clients?clientId=acti-web", token=token)
    require(len(clients) == 1, "idp_client_not_unique")
    client = clients[0]
    require(client.get("attributes", {}).get("actiManaged") == MARKER
            and client.get("attributes", {}).get("pkce.code.challenge.method") == "S256"
            and client.get("protocol") == "openid-connect" and client.get("enabled")
            and not client.get("publicClient") and client.get("standardFlowEnabled")
            and not any(client.get(key) for key in (
                "implicitFlowEnabled", "directAccessGrantsEnabled", "serviceAccountsEnabled"))
            and client.get("redirectUris") == [values["ACTI_ORIGIN"] + "/api/v1/auth/oidc/callback"]
            and client.get("webOrigins") == [], "unsafe_idp_client")
    actual_secret = request("GET", "/admin/realms/acti/clients/" + client["id"] + "/client-secret", token=token)
    require(secrets.compare_digest(actual_secret.get("value", ""), state["client_secret"]),
            "idp_client_secret_mismatch")
    # Keycloak 26 drops undeclared attributes. The ownership marker is admin-only.
    profile = request("GET", "/admin/realms/acti/users/profile", token=token)
    # AD may legitimately lack these fields; READ_ONLY federation cannot fill them.
    # ACTI authorizes the linked UUID, never the profile's name or email.
    changed_profile = False
    for item in profile["attributes"]:
        if item["name"] in {"email", "firstName", "lastName"} and "required" in item:
            item.pop("required")
            changed_profile = True
    markers = [item for item in profile["attributes"] if item["name"] == "actiManaged"]
    if not markers:
        profile["attributes"].append({"name": "actiManaged", "multivalued": False,
                                      "permissions": {"view": ["admin"], "edit": ["admin"]}})
        changed_profile = True
    else:
        require(len(markers) == 1 and markers[0].get("permissions") == {
            "view": ["admin"], "edit": ["admin"]}, "unsafe_idp_ownership_attribute")
    if changed_profile:
        request("PUT", "/admin/realms/acti/users/profile", profile, token)
    for account in state["pilot_accounts"]:
        users = request("GET", "/admin/realms/acti/users?" + urlencode({
            "username": account["login"], "exact": "true"}), token=token)
        if not users:
            request("POST", "/admin/realms/acti/users", {
                "username": account["login"], "enabled": True,
                "firstName": "ACTI", "lastName": "Pilot " + account["role"],
                "email": account["login"] + "@example.invalid", "emailVerified": True,
                "credentials": [{"type": "password", "value": account["password"], "temporary": False}],
                "attributes": {"actiManaged": [MARKER]},
            }, token)
            users = request("GET", "/admin/realms/acti/users?" + urlencode({
                "username": account["login"], "exact": "true"}), token=token)
        require(len(users) == 1 and users[0].get("attributes", {}).get("actiManaged") == [MARKER],
                "foreign_idp_pilot")
        account["subject"] = users[0]["id"]
    bootstrap = request("GET", "/admin/realms/master/users?" + urlencode({
        "username": state["bootstrap"]["login"], "exact": "true"}), token=token)
    for user in bootstrap:
        request("DELETE", "/admin/realms/master/users/" + user["id"], token=token)
    state["configured"] = True
    save_state(state)
    values.update({"ACTI_OIDC_ENABLED": "true", "ACTI_OIDC_ISSUER": base + "/realms/acti",
                   "ACTI_OIDC_CLIENT_ID": "acti-web", "ACTI_OIDC_CLIENT_SECRET": state["client_secret"],
                   "ACTI_OIDC_LABEL": "единый вход ACTI"})
    values.pop("ACTI_IDP_BOOTSTRAP_ADMIN", None)
    values.pop("ACTI_IDP_BOOTSTRAP_PASSWORD", None)
    values.pop("KC_BOOTSTRAP_ADMIN_USERNAME", None)
    values.pop("KC_BOOTSTRAP_ADMIN_PASSWORD", None)
    save_env(values)
    return {"status": "configured", "pilot_accounts": len(state["pilot_accounts"]),
            "domain_federation": False, "restart_required": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "configure"))
    args = parser.parse_args()
    require(os.getuid() == 0, "root_required")
    spec = importlib.util.spec_from_file_location("acti_restore", Path(__file__).with_name("restore-verify.py"))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    values = helper.parse_env(helper.protected(ENV).read_text())
    helper.verify_expected_mounts({
        str(helper.POSTGRES_ROOT): values.get("ACTI_POSTGRES_UUID", ""),
        str(helper.DOCUMENTS_ROOT): values.get("ACTI_DOCUMENTS_UUID", ""),
    })
    with open("/run/lock/acti-idp-provision.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = prepare(values, helper) if args.action == "prepare" else configure(values, helper)
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(json.dumps({"status": "FAIL", "code": "idp_provision_failed"}))
        raise SystemExit(1) from None
