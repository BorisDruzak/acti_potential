"""Deployment boundaries for the optional identity provider."""

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_idp_profile_never_exposes_provider_or_worker_secrets():
    env = dict(os.environ, POSTGRES_IMAGE="postgres:16", REDIS_IMAGE="redis:7",
               POSTGRES_PASSWORD="synthetic", ACTI_DATABASE_URL="postgresql://example",
               ACTI_MIGRATION_DATABASE_URL="postgresql://example", ACTI_APP_PASSWORD="synthetic",
               ACTI_OWNER_PASSWORD="synthetic", ACTI_ORIGIN="https://example.invalid",
               NODE_IMAGE="node:24", NGINX_IMAGE="nginx:stable", ACTI_SESSION_SECRET="synthetic",
               ACTI_SOURCE_SHA="test", ACTI_OIDC_CLIENT_SECRET="synthetic-oidc-secret",
               ACTI_IDP_DB_PASSWORD="synthetic-idp-db-secret")
    config = json.loads(subprocess.check_output(
        ["docker", "compose", "--profile", "idp", "-f", str(ROOT / "deploy/compose.yaml"),
         "config", "--format", "json"], env=env, text=True))
    services = config["services"]
    provider = services["keycloak"]
    assert not provider.get("ports")
    assert provider["restart"] == "no"
    assert provider["read_only"] and "ALL" in provider["cap_drop"]
    assert int(provider["mem_limit"]) == 1536 * 1024**2
    assert provider["environment"]["KC_DB_SCHEMA"] == "keycloak"
    assert provider["environment"]["KC_DB_USERNAME"] == "acti_keycloak"
    assert "--optimized" in provider["command"]
    assert "--http-enabled=true" in provider["command"]
    assert provider["environment"]["KC_HOSTNAME"] == "https://example.invalid/idp"
    # Keycloak rejects present-but-empty bootstrap values after initial setup.
    assert provider["environment"].get("KC_BOOTSTRAP_ADMIN_USERNAME") is None
    assert provider["environment"].get("KC_BOOTSTRAP_ADMIN_PASSWORD") is None
    for name in ("worker", "migrate"):
        assert not any(key.startswith("ACTI_OIDC_") for key in services[name]["environment"])
        assert "identity" not in services[name]["networks"]
    assert "identity" in services["api"]["networks"]
    assert not services["api"].get("ports")
    assert services["api"]["environment"]["ACTI_OIDC_CA_FILE"] == "/etc/acti-ca/ca.crt"
    ca = next(v for v in services["api"]["volumes"] if v["target"] == "/etc/acti-ca/ca.crt")
    assert ca["read_only"] and not ca.get("bind", {}).get("create_host_path", False)


def test_idp_proxy_is_lazy_and_preserves_tls_origin():
    source = (ROOT / "deploy/nginx.conf").read_text()
    location = source.split("location /idp/ {", 1)[1].split("\n    }", 1)[0]
    assert "set $identity_upstream keycloak:8080" in location
    assert "proxy_pass http://$identity_upstream;" in location
    assert "proxy_set_header X-Forwarded-Proto https" in location
    assert "proxy_set_header X-Forwarded-Port 443" in location
    assert "Content-Security-Policy" not in location
    image = (ROOT / "deploy/keycloak.Dockerfile").read_text()
    assert "@sha256:" in image and "kc.sh build --db=postgres --health-enabled=true" in image
    assert "--http-relative-path=/idp" in image and "--http-management-relative-path=/" in image
