import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_compose_is_private_and_migration_gated():
    env = dict(
        __import__("os").environ,
        POSTGRES_IMAGE="postgres:16",
        REDIS_IMAGE="redis:7",
        POSTGRES_PASSWORD="synthetic-test-password",
        ACTI_DATABASE_URL="postgresql://example",
        ACTI_MIGRATION_DATABASE_URL="postgresql://example",
        ACTI_APP_PASSWORD="synthetic",
        ACTI_OWNER_PASSWORD="synthetic",
        ACTI_ORIGIN="https://example.invalid",
        NODE_IMAGE="node:24",
        NGINX_IMAGE="nginx:stable",
        ACTI_SESSION_SECRET="synthetic",
        ACTI_SOURCE_SHA="test",
        ACTI_RELEASE_DIR=str(ROOT),
    )
    result = subprocess.check_output(
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "deploy/compose.yaml"),
            "config",
            "--format",
            "json",
        ],
        env=env,
        text=True,
    )
    config = json.loads(result)
    services = config["services"]
    assert {"postgres", "redis", "migrate", "api", "worker", "frontend"} <= set(services)
    for name in ["postgres", "redis", "api", "worker", "migrate"]:
        assert not services[name].get("ports")
        assert services[name].get("restart", "no") == "no"
    assert services["api"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert (
        services["worker"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    )
    volume = services["postgres"]["volumes"][0]
    assert volume["source"] == "/srv/acti/postgresql/data"
    # Compose omits a normalized false value; short-syntax binds emit true.
    assert volume.get("bind", {}).get("create_host_path", False) is False
    assert "create_host_path: false" in (ROOT / "deploy/compose.yaml").read_text()


def test_systemd_and_restore_guards_are_present():
    unit = (ROOT / "deploy/acti-v3.service").read_text()
    assert "RequiresMountsFor=/srv/acti/postgresql /srv/acti/documents" in unit
    assert "ExecStartPre=" in unit
    restore = (ROOT / "deploy/restore-test.sh").read_text()
    assert "acti_restore_test" in restore
    assert "preflight" in restore
