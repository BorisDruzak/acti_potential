#!/usr/bin/env python3
"""Configure ACTI's owned Keycloak LDAP bridge. Does not write to AD.

The default configuration excludes the service reader from interactive login.
--reader-test temporarily limits login to that reader for a controlled browser
proof; run the default mode immediately afterwards to remove that exception.
"""

import argparse
import importlib.util
import json
import os
import re
import ssl
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

ROOT = Path("/etc/acti-v3")
MARKER = "acti-managed-ad-readonly-v1"


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def require_owned_component(components, proof):
    require(len(components) <= 1, "ambiguous_ldap_provider")
    if components:
        require(isinstance(proof, dict) and proof.get("owner") == MARKER
                and proof.get("component_id") == components[0].get("id"), "foreign_ldap_provider")
    else:
        require(proof is None, "owned_ldap_provider_missing")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("idp_admin_redirect_forbidden")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reader-test", action="store_true")
    args = parser.parse_args()
    require(os.getuid() == 0, "root_required")
    spec = importlib.util.spec_from_file_location("acti_restore", Path(__file__).with_name("restore-verify.py"))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    provider = json.loads(helper.protected(ROOT / "idp/provision.json").read_text())
    ldap = json.loads(helper.protected(ROOT / "idp/ldap.json").read_text())
    values = helper.parse_env(helper.protected(ROOT / "runtime.env").read_text())
    require(provider.get("configured") and provider.get("owner") == "acti-managed-keycloak-v1"
            and provider["origin"] == values["ACTI_ORIGIN"], "foreign_idp_state")
    address = urlsplit(ldap["ldap_url"])
    require(address.scheme == "ldaps" and address.hostname == ldap["dc_fqdn"]
            and address.port == 636 and not address.username and not address.password
            and not address.query and not address.fragment and address.path in ("", "/"),
            "invalid_protected_ldap_url")
    require(re.fullmatch(r"acti_[a-z0-9_]{1,15}", ldap["reader_sam"]), "unexpected_ldap_reader")
    base = values["ACTI_ORIGIN"] + "/idp"
    context = ssl.create_default_context(cafile=values.get("ACTI_HTTPS_CA_FILE", str(ROOT / "tls/ca.crt")))
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())

    def call(method, path, body=None, token=None, form=False):
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
            body = (urlencode(body) if form else json.dumps(body)).encode()
        try:
            with opener.open(Request(base + path, data=body, headers=headers, method=method), timeout=15) as response:
                data = response.read(2 * 1024**2 + 1)
                require(len(data) <= 2 * 1024**2, "idp_admin_response_too_large")
                return json.loads(data) if data else None
        except HTTPError:
            raise RuntimeError("idp_admin_request_rejected") from None

    account = provider["administrator"]
    token = call("POST", "/realms/master/protocol/openid-connect/token", {
        "client_id": "admin-cli", "grant_type": "password", "username": account["login"],
        "password": account["password"],
    }, form=True)["access_token"]
    realm = call("GET", "/admin/realms/acti", token=token)
    components = call("GET", "/admin/realms/acti/components?" + urlencode({
        "type": "org.keycloak.storage.UserStorageProvider", "name": "acti-ad-readonly"}), token=token)
    proof_path = ROOT / "idp/federation-proof.json"
    owned = json.loads(helper.protected(proof_path).read_text()) if proof_path.exists() else None
    # Keycloak filters unknown component properties; pin ownership outside its config.
    require_owned_component(components, owned)
    reader = ldap["reader_sam"]
    search_filter = ("(sAMAccountName=" + reader + ")" if args.reader_test else
                     "(&(objectCategory=person)(!(sAMAccountName=" + reader + ")))")
    raw = {
        "enabled": "true", "priority": "0", "vendor": "ad",
        "editMode": "READ_ONLY", "importEnabled": "true", "syncRegistrations": "false",
        "connectionUrl": ldap["ldap_url"], "startTls": "false", "authType": "simple",
        "bindDn": ldap["reader_dn"], "bindCredential": ldap["password"],
        "usersDn": ldap["base_dn"], "usernameLDAPAttribute": "sAMAccountName",
        "rdnLDAPAttribute": "cn", "uuidLDAPAttribute": "objectGUID",
        "userObjectClasses": "person, organizationalPerson, user", "searchScope": "2",
        "connectionPooling": "true", "connectionTimeout": "3000", "readTimeout": "5000",
        "useTruststoreSpi": "always", "referral": "ignore", "pagination": "true",
        "fullSyncPeriod": "-1", "changedSyncPeriod": "-1", "cachePolicy": "NO_CACHE",
        "allowKerberosAuthentication": "false", "useKerberosForPasswordAuthentication": "false",
        "usePasswordModifyExtendedOp": "false", "validatePasswordPolicy": "false",
        "trustEmail": "false", "debug": "false", "customUserSearchFilter": search_filter,
    }
    body = {"name": "acti-ad-readonly", "providerId": "ldap",
            "providerType": "org.keycloak.storage.UserStorageProvider", "parentId": realm["id"],
            "config": {key: [value] for key, value in raw.items()}}
    if components:
        body["id"] = components[0]["id"]
        call("PUT", "/admin/realms/acti/components/" + body["id"], body, token)
    else:
        call("POST", "/admin/realms/acti/components", body, token)
    components = call("GET", "/admin/realms/acti/components?" + urlencode({
        "type": "org.keycloak.storage.UserStorageProvider", "name": "acti-ad-readonly"}), token=token)
    require(len(components) == 1 and components[0]["config"].get("editMode") == ["READ_ONLY"]
            and components[0]["config"].get("syncRegistrations") == ["false"], "ldap_readonly_not_confirmed")
    proof = {"owner": MARKER, "status": "configured", "mode": "reader_test" if args.reader_test else "staff",
             "read_only": True, "scheduled_sync": False, "ad_writes": False,
             "component_id": components[0]["id"]}
    path = proof_path
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(proof, stream)
    print(json.dumps({key: value for key, value in proof.items() if key not in {"component_id", "owner"}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(json.dumps({"status": "FAIL", "code": "idp_federation_failed"}))
        raise SystemExit(1) from None
