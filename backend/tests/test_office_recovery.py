"""Copy-first recovery never writes binary revisions or releases another editor."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.identity.models import AuthSession
from app.platform.models import AuditEvent
from app.registry.models import (
    BinaryRevision,
    Document,
    LogicalOriginal,
    OfficeAgentCommand,
    OfficeAgentConnection,
    OfficeEditSession,
)
from tests.test_documents import headers
from tests.test_office_agent import BASE, agent_headers, command, pulse
from tests.test_office_sync import credential, editing, endpoint, exchange, launch


def modern(client, edit):
    response = client.post(
        BASE + "/agents/" + edit["agent"]["connection_id"] + "/heartbeat",
        headers=agent_headers(edit["agent"]),
        json={"client_version": "1.4.0"},
    )
    assert response.status_code == 200, response.text


def recovery(client, edit, command_id, **override):
    body = {"command_id": command_id, "local_sha256": "a" * 64, "document_closed": True}
    body.update(override)
    return client.post(endpoint(edit, "recover"), headers=credential(edit), json=body)


def ready(client, factory):
    card, edit = editing(client, factory)
    modern(client, edit)
    queued = command(client, edit, "recover")
    assert queued.status_code == 200, queued.text
    return card, edit, queued.json()["id"]


def test_optional_telemetry_is_bounded_fresh_scoped_and_old_clients_are_unknown(client, factory):
    _, edit = editing(client, factory)
    assert edit["capabilities"] == ["queue-telemetry-v1", "recovery-v1"]
    modern(client, edit)
    url = endpoint(edit, "heartbeat")
    for invalid in (-1, 3, True, "1"):
        assert client.post(url, headers=credential(edit), json={
            "state": "editing", "pending_uploads": invalid,
        }).status_code == 422
    assert client.post(url, headers=credential(edit), json={
        "state": "error", "error_code": r"C:\private\token",
    }).status_code == 422
    reply = client.post(url, headers=credential(edit), json={
        "state": "local_saved", "pending_uploads": 2, "error_code": "network",
    })
    assert reply.status_code == 200, reply.text
    status = client.get(BASE + "/agent/status").json()["documents"][0]
    assert (status["pending_uploads"], status["error_code"], status["telemetry_fresh"]) == (
        2, "network", True,
    )
    assert datetime.fromisoformat(status["telemetry_seen_at"]) > datetime.now(UTC) - timedelta(seconds=5)
    with factory.begin() as db:
        db.get(OfficeEditSession, UUID(edit["session_id"])).last_seen_at -= timedelta(seconds=36)
    status = client.get(BASE + "/agent/status").json()["documents"][0]
    assert status["pending_uploads"] is None and status["error_code"] is None
    assert status["telemetry_fresh"] is False
    legacy = client.post(url, headers=credential(edit), json={"state": "editing"})
    assert legacy.status_code == 200
    assert legacy.json()["capabilities"] == ["queue-telemetry-v1", "recovery-v1"]
    status = client.get(BASE + "/agent/status").json()["documents"][0]
    assert status["pending_uploads"] is None and status["telemetry_fresh"] is False
    # File status readable by another browser must not expose owned telemetry.
    with factory.begin() as db:
        row = db.get(OfficeEditSession, UUID(edit["session_id"]))
        row.pending_uploads, row.error_code = 2, "conflict"
    client.post("/api/v1/auth/login", json={
        "login": "admin", "password": "synthetic-password-for-tests",
    }, headers={"Origin": "https://testserver"})
    status = client.get(BASE + "/files/" + edit["file_id"] + "/status").json()
    assert status["pending_uploads"] is None and status["error_code"] is None


def test_recover_is_version_gated_both_at_issue_and_delivery(client, factory):
    _, edit = editing(client, factory)
    pulse(client, edit["agent"])
    denied = command(client, edit, "recover")
    assert denied.status_code == 409 and denied.json()["error"]["code"] == "client_upgrade_required"
    modern(client, edit)
    queued = command(client, edit, "recover").json()
    response = client.post(BASE + "/agents/" + edit["agent"]["connection_id"] + "/heartbeat",
        headers=agent_headers(edit["agent"]), json={"client_version": "1.3.8"})
    assert response.status_code == 200 and response.json()["commands"] == []
    with factory() as db:
        assert db.get(OfficeAgentCommand, UUID(queued["id"])).state == "failed"


def test_recover_finishes_only_old_session_and_retries_lost_ack_without_duplicate_audit(client, factory):
    _, edit, command_id = ready(client, factory)
    download_url = BASE + "/files/" + edit["file_id"] + "/download"
    downloaded = client.get(download_url)
    assert downloaded.status_code == 200
    original_bytes = downloaded.content
    with factory.begin() as db:
        old = db.get(OfficeEditSession, UUID(edit["session_id"]))
        old.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        original = db.get(LogicalOriginal, old.original_id)
        before = original.current_file_id, original.revision
    _, ticket = launch(client, edit["file_id"])
    next_edit = exchange(client, ticket).json()
    modern(client, next_edit)
    visible = client.get(BASE + "/agent/status").json()["documents"]
    assert {item["session_id"] for item in visible} == {edit["session_id"], next_edit["session_id"]}
    with factory() as db:
        lease = db.get(OfficeEditSession, UUID(next_edit["session_id"])).lease_expires_at
    result = recovery(client, edit, command_id)
    assert result.status_code == 200, result.text
    assert result.json()["state"] == "finished"
    with factory.begin() as db:
        db.get(OfficeAgentCommand, UUID(command_id)).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert recovery(client, edit, command_id).status_code == 200
    assert recovery(client, edit, command_id, local_sha256="b" * 64).status_code == 409
    assert client.post(endpoint(edit, "save"), headers=credential(edit)).status_code != 200
    downloaded = client.get(download_url)
    assert downloaded.status_code == 200 and downloaded.content == original_bytes
    visible = client.get(BASE + "/agent/status").json()["documents"]
    assert [item["session_id"] for item in visible] == [next_edit["session_id"]]
    with factory() as db:
        original = db.get(LogicalOriginal, UUID(edit["file_id"]))
        assert (original.current_file_id, original.revision) == before
        assert db.scalar(select(func.count()).select_from(BinaryRevision)) == 1
        newer = db.get(OfficeEditSession, UUID(next_edit["session_id"]))
        assert newer.finished_at is None and newer.lease_expires_at == lease
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.action == "document.sync.recovered")))
        assert len(events) == 1
        assert events[0].details == {"session_id": edit["session_id"],
            "command_id": command_id, "local_sha256": "a" * 64}


@pytest.mark.parametrize("mutation,expected", [
    ("unknown_command", 404), ("another_session", 404), ("another_connection", 404),
    ("another_action", 404), ("expired_command", 409), ("expired_auth", 401),
    ("expired_token", 401), ("expired_connection", 401), ("revoked_acl", 404),
])
def test_recover_revalidates_command_session_connection_auth_and_acl(client, factory, mutation, expected):
    card, edit, command_id = ready(client, factory)
    with factory.begin() as db:
        row = db.get(OfficeEditSession, UUID(edit["session_id"]))
        queued = db.get(OfficeAgentCommand, UUID(command_id))
        expired = datetime.now(UTC) - timedelta(seconds=1)
        if mutation == "unknown_command":
            command_id = str(uuid4())
        elif mutation == "another_session":
            row.finished_at = datetime.now(UTC)
            row.lease_expires_at = expired
        elif mutation == "another_connection":
            row.connection_id = None
        elif mutation == "another_action":
            queued.action = "retry"
        elif mutation == "expired_command":
            queued.expires_at = expired
        elif mutation == "expired_auth":
            db.get(AuthSession, row.auth_session_id).expires_at = expired
        elif mutation == "expired_token":
            row.expires_at = expired
        elif mutation == "expired_connection":
            db.get(OfficeAgentConnection, row.connection_id).expires_at = expired
        elif mutation == "revoked_acl":
            db.get(Document, UUID(card["id"])).deleted_at = datetime.now(UTC)
    if mutation == "another_session":
        _, ticket = launch(client, edit["file_id"])
        edit = exchange(client, ticket).json()
        modern(client, edit)
    result = recovery(client, edit, command_id)
    assert result.status_code == expected, result.text
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action == "document.sync.recovered")) == 0


def test_recover_needs_session_bearer_closed_document_and_server_receipt_for_success(client, factory):
    _, edit, command_id = ready(client, factory)
    for value in (False, "true", 1, None):
        assert recovery(client, edit, command_id, document_closed=value).status_code == 422
    assert recovery(client, edit, command_id, local_sha256="a" * 63).status_code == 422
    assert client.post(endpoint(edit, "recover"), headers=headers(client), json={
        "command_id": command_id, "local_sha256": "a" * 64, "document_closed": True,
    }).status_code == 401
    assert client.post(endpoint(edit, "recover"), headers=agent_headers(edit["agent"]), json={
        "command_id": command_id, "local_sha256": "a" * 64, "document_closed": True,
    }).status_code == 401
    pulse(client, edit["agent"], [{"id": command_id, "ok": True}])
    with factory() as db:
        assert db.get(OfficeEditSession, UUID(edit["session_id"])).finished_at is None
        assert db.get(OfficeAgentCommand, UUID(command_id)).state != "succeeded"


def test_recover_refusal_uses_only_safe_closed_word_error_for_expired_lease(client, factory):
    _, edit, command_id = ready(client, factory)
    with factory.begin() as db:
        db.get(OfficeEditSession, UUID(edit["session_id"])).lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    pulse(client, edit["agent"], [{"id": command_id, "ok": False, "error": "file_busy"}])
    status = client.get(BASE + "/agent/status").json()["documents"][0]
    assert status["command"]["error_code"] == "file_busy"
    assert status["can_recover"] is True
    with factory() as db:
        assert db.get(OfficeEditSession, UUID(edit["session_id"])).finished_at is None


def test_lost_ack_retry_still_revalidates_current_auth_and_write_acl(client, factory):
    from app.registry.models import RegistryAccess

    card, edit, command_id = ready(client, factory)
    assert recovery(client, edit, command_id).status_code == 200
    with factory.begin() as db:
        access = db.get(RegistryAccess, (UUID(card["registry_id"]), UUID(card["files"][0]["created_by"])))
        access.permissions = ["read", "update", "download"]
    assert recovery(client, edit, command_id).status_code == 403
    with factory.begin() as db:
        row = db.get(OfficeEditSession, UUID(edit["session_id"]))
        db.get(AuthSession, row.auth_session_id).revoked_at = datetime.now(UTC)
    assert recovery(client, edit, command_id).status_code == 401


def test_recovery_migration_preserves_legacy_session_and_refuses_receipt_removal(client, factory):
    from pathlib import Path

    from alembic import command as migration
    from alembic.config import Config
    from sqlalchemy import text

    _, edit = editing(client, factory)
    with factory.kw["bind"].begin() as connection:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        before = connection.execute(text("SELECT id, original_id, token_hash FROM office_edit_sessions")).all()
        migration.downgrade(config, "0021_office_agent")
        migration.upgrade(config, "head")
        assert connection.execute(text("SELECT id, original_id, token_hash FROM office_edit_sessions")).all() == before
        assert connection.execute(text("SELECT pending_uploads, error_code, telemetry_seen_at FROM office_edit_sessions")).one() == (None, None, None)
    modern(client, edit)
    command_id = command(client, edit, "recover").json()["id"]
    assert recovery(client, edit, command_id).status_code == 200
    with factory.kw["bind"].begin() as connection:
        config.attributes["connection"] = connection
        with pytest.raises(RuntimeError, match="recovery evidence"), connection.begin_nested():
            migration.downgrade(config, "0021_office_agent")
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0022_office_recovery"


def test_unfinished_projection_stays_scoped_to_user_and_browser(client, factory):
    from app.identity.models import Role, User, UserRole
    from app.identity.sessions import csrf_for_token, issue_session
    from app.registry.models import RegistryAccess

    card, edit = editing(client, factory)
    modern(client, edit)
    with factory.begin() as db:
        db.get(OfficeEditSession, UUID(edit["session_id"])).lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        other = User(display_name="Synthetic other recovery editor")
        db.add(other)
        db.flush()
        db.add(UserRole(user_id=other.id, role_id=db.scalar(select(Role.id).where(Role.code == "admin"))))
        db.add(RegistryAccess(registry_id=UUID(card["registry_id"]), user_id=other.id,
            permissions=["read", "update", "upload", "download"]))
        access = issue_session(db, other.id, datetime.now(UTC))
    other_headers = {"Cookie": "acti_session=" + access.token, "Origin": "https://testserver",
        "X-CSRF-Token": csrf_for_token(access.token)}
    queued = client.post(BASE + "/launch", headers=other_headers, json={"file_id": edit["file_id"]})
    assert queued.status_code == 200, queued.text
    ticket = parse_qs(urlsplit(queued.json()["uri"]).query)["ticket"][0]
    next_edit = exchange(client, ticket).json()
    modern(client, next_edit)
    assert [item["session_id"] for item in client.get(BASE + "/agent/status").json()["documents"]] == [edit["session_id"]]
    assert [item["session_id"] for item in client.get(BASE + "/agent/status", headers=other_headers).json()["documents"]] == [next_edit["session_id"]]
    assert command(client, edit, "recover", request_headers=other_headers).status_code == 404
