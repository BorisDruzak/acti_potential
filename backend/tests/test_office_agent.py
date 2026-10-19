"""Browser-owned agent presence and bounded command relay acceptance."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from sqlalchemy import select

from app.identity.models import AuthSession
from app.identity.sessions import csrf_for_token, issue_session
from app.registry.models import OfficeEditSession
from tests.test_documents import headers
from tests.test_office_open import original
from tests.test_office_sync import credential, docx, editing, endpoint, save

BASE = "/api/v1/office-sync"


def agent_headers(agent):
    return {"Authorization": "Bearer " + agent["token"]}


def pulse(client, agent, results=None):
    return client.post(
        BASE + "/agents/" + agent["connection_id"] + "/heartbeat",
        headers=agent_headers(agent),
        json={"command_results": results or []},
    )


def connect(client):
    reply = client.post(BASE + "/agent/connect", headers=headers(client))
    assert reply.status_code == 200, reply.text
    ticket = parse_qs(urlsplit(reply.json()["uri"]).query)["ticket"][0]
    result = client.post(
        BASE + "/agent/exchange", json={"ticket": ticket, "client_version": "1.1.0"}
    )
    assert result.status_code == 200, result.text
    return result.json(), ticket


def command(client, edit, action="reveal", request_headers=None):
    return client.post(
        BASE + "/agent/commands",
        headers=request_headers or headers(client),
        json={"session_id": edit["session_id"], "action": action},
    )


def test_connect_presence_without_documents_is_single_use_rotates_and_scoped(client, factory):
    original(client, factory)
    assert client.post(BASE + "/agent/connect").status_code == 403
    agent, ticket = connect(client)
    assert set(agent) == {"connection_id", "token", "expires_at"}
    assert (
        client.post(
            BASE + "/agent/exchange", json={"ticket": ticket, "client_version": "1.1.0"}
        ).status_code
        == 409
    )
    assert client.get(BASE + "/agent/status").json()["online"] is False
    assert pulse(client, agent).json() == {"commands": []}
    status = client.get(BASE + "/agent/status").json()
    assert status["online"] is True and status["documents"] == []
    assert status["client_version"] == "1.1.0" and len(status["connections"]) == 1
    assert agent["token"] not in str(status)
    assert (
        client.get(
            BASE + "/sessions/" + agent["connection_id"] + "/download", headers=agent_headers(agent)
        ).status_code
        == 401
    )
    renewed = client.post(
        BASE + "/agents/" + agent["connection_id"] + "/renew", headers=agent_headers(agent)
    )
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["token"] != agent["token"]
    assert pulse(client, agent).status_code == 401
    assert pulse(client, renewed.json()).status_code == 200


def test_launch_binds_connection_commands_are_browser_owned_and_results_do_not_leak(
    client, factory
):
    card, edit = editing(client, factory)
    agent = edit["agent"]
    assert client.get(endpoint(edit, "download"), headers=agent_headers(agent)).status_code == 401
    assert pulse(client, agent).status_code == 200
    with factory.begin() as db:
        alternate = issue_session(db, UUID(card["files"][0]["created_by"]), datetime.now(UTC))
    other_headers = {
        "Cookie": "acti_session=" + alternate.token,
        "Origin": "https://testserver",
        "X-CSRF-Token": csrf_for_token(alternate.token),
    }
    assert client.get(BASE + "/agent/status", headers=other_headers).json()["documents"] == []
    assert command(client, edit, request_headers=other_headers).status_code == 404
    assert command(client, edit, "arbitrary.exe").status_code == 422
    queued = command(client, edit).json()
    assert queued["state"] == "pending"
    pending = pulse(client, agent).json()["commands"]
    assert len(pending) == 1
    assert pending[0]["id"] == queued["id"]
    assert pending[0]["session_id"] == edit["session_id"]
    assert pending[0]["action"] == "reveal"
    assert datetime.fromisoformat(pending[0]["expires_at"]) > datetime.now(UTC)
    # Redelivery uses exactly the same identity until acknowledged.
    assert pulse(client, agent).json()["commands"] == pending
    assert (
        pulse(
            client,
            agent,
            [{"id": queued["id"], "ok": False, "error": r"C:\private\secret.docx token=secret"}],
        ).status_code
        == 200
    )
    status = client.get(BASE + "/agent/status").json()
    result = status["documents"][0]["command"]
    assert result["state"] == "failed" and result["error"]
    assert "private" not in str(status) and "token=secret" not in str(status)
    # A duplicate or contradictory acknowledgement cannot change the terminal result.
    pulse(client, agent, [{"id": queued["id"], "ok": True}])
    assert client.get(BASE + "/agent/status").json()["documents"][0]["command"]["state"] == "failed"
    assert (
        pulse(client, agent, [{"id": queued["id"], "ok": False, "error": "x" * 257}]).status_code
        == 422
    )


def test_observed_open_close_transitions_preserve_server_ack_and_upload_after_close(
    client, factory
):
    _, edit = editing(client, factory)

    def beat(opened):
        return client.post(
            endpoint(edit, "heartbeat"),
            json={"state": "editing", "document_open": opened},
            headers=credential(edit),
        )

    first = beat(True)
    assert first.status_code == 200, first.text
    assert first.json()["document_open"] is True and first.json()["opened_at"]
    assert beat(True).json()["opened_at"] == first.json()["opened_at"]
    closed = beat(False).json()
    assert closed["closed_at"] and closed["document_open"] is False
    assert closed["last_saved_at"] is None
    assert beat(False).json()["closed_at"] == closed["closed_at"]
    ack = save(client, edit, docx("pending bytes upload after Word closes"))
    assert ack.status_code == 200, ack.text
    after = beat(False).json()
    assert after["state"] == "saved" and after["last_saved_at"]
    assert after["document_open"] is False and after["closed_at"] == closed["closed_at"]


def test_agent_logout_invalidates_presence_and_commands(client, factory):
    _, edit = editing(client, factory)
    agent = edit["agent"]
    pulse(client, agent)
    assert command(client, edit).status_code == 200
    with factory.begin() as db:
        row = db.get(OfficeEditSession, UUID(edit["session_id"]))
        db.get(AuthSession, row.auth_session_id).revoked_at = datetime.now(UTC)
    assert pulse(client, agent).status_code == 401


def test_expired_commands_never_dispatch_and_acl_is_rechecked(client, factory):
    from app.registry.models import (
        Document,
        LogicalOriginal,
        OfficeAgentCommand,
        OfficeAgentConnection,
    )

    _, edit = editing(client, factory)
    agent = edit["agent"]
    pulse(client, agent)
    queued = command(client, edit).json()
    with factory.begin() as db:
        db.get(OfficeAgentCommand, UUID(queued["id"])).expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
    assert pulse(client, agent).json()["commands"] == []
    assert (
        client.get(BASE + "/agent/status").json()["documents"][0]["command"]["state"] == "expired"
    )
    denied = command(client, edit).json()
    with factory.begin() as db:
        row = db.get(OfficeEditSession, UUID(edit["session_id"]))
        anchor = db.get(LogicalOriginal, row.original_id)
        db.get(Document, anchor.document_id).deleted_at = datetime.now(UTC)
    assert pulse(client, agent).json()["commands"] == []
    assert client.get(BASE + "/agent/status").json()["documents"] == []
    with factory() as db:
        assert db.get(OfficeAgentCommand, UUID(denied["id"])).state == "failed"
    with factory.begin() as db:
        db.get(OfficeAgentConnection, UUID(agent["connection_id"])).last_seen_at = datetime.now(
            UTC
        ) - timedelta(seconds=36)
    assert client.get(BASE + "/agent/status").json()["online"] is False
    assert command(client, edit).status_code in {404, 409}


def test_finish_ack_after_document_finish_succeeds_and_initial_false_is_unknown(client, factory):
    _, edit = editing(client, factory)
    agent = edit["agent"]
    assert edit["document_open"] is None
    initial = client.post(
        endpoint(edit, "heartbeat"),
        json={"state": "editing", "document_open": False},
        headers=credential(edit),
    )
    assert initial.json()["document_open"] is None and initial.json()["closed_at"] is None
    pulse(client, agent)
    queued = command(client, edit, "finish").json()
    assert client.post(endpoint(edit, "finish"), headers=credential(edit)).status_code == 200
    assert pulse(client, agent, [{"id": queued["id"], "ok": True}]).status_code == 200
    status = client.get(BASE + "/agent/status").json()["documents"][0]
    assert status["state"] == "finished" and status["command"]["state"] == "succeeded"


def test_expired_connect_ticket_and_blocked_user_cannot_exchange_or_pulse(client, factory):
    from app.identity.models import User
    from app.registry.models import OfficeAgentTicket

    card = original(client, factory)
    agent, ticket = connect(client)
    with factory.begin() as db:
        pending = db.scalar(
            select(OfficeAgentTicket).where(OfficeAgentTicket.exchanged_at.is_not(None))
        )
        pending.exchanged_at = None
        pending.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert (
        client.post(
            BASE + "/agent/exchange", json={"ticket": ticket, "client_version": "1.1.0"}
        ).status_code
        == 409
    )
    with factory.begin() as db:
        db.get(User, UUID(card["files"][0]["created_by"])).active = False
    assert pulse(client, agent).status_code == 401


def test_reconnect_and_launch_rotate_one_browser_connection_without_losing_document(
    client, factory
):
    from tests.test_office_sync import exchange, launch

    _, edit = editing(client, factory)
    first = edit["agent"]
    pulse(client, first)
    renewed, _ = connect(client)
    assert renewed["connection_id"] == first["connection_id"]
    assert renewed["token"] != first["token"]
    assert pulse(client, first).status_code == 401
    assert pulse(client, renewed).status_code == 200
    assert (
        save(client, edit, docx("document credential survives presence rotation")).status_code
        == 200
    )
    assert client.post(endpoint(edit, "finish"), headers=credential(edit)).status_code == 200
    _, ticket = launch(client, edit["file_id"])
    next_edit = exchange(client, ticket).json()
    assert next_edit["agent"]["connection_id"] == first["connection_id"]
    assert pulse(client, renewed).status_code == 401
    assert pulse(client, next_edit["agent"]).status_code == 200
    documents = client.get(BASE + "/agent/status").json()["documents"]
    assert len(documents) == 1 and documents[0]["session_id"] == next_edit["session_id"]


def test_another_browser_connection_cannot_ack_or_read_original_commands(client, factory):
    _, edit = editing(client, factory)
    first = edit["agent"]
    pulse(client, first)
    queued = command(client, edit).json()
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"login": "admin", "password": "synthetic-password-for-tests"},
            headers={"Origin": "https://testserver"},
        ).status_code
        == 200
    )
    other, _ = connect(client)
    assert other["connection_id"] != first["connection_id"]
    assert pulse(client, other, [{"id": queued["id"], "ok": True}]).json()["commands"] == []
    assert pulse(client, first).json()["commands"][0]["id"] == queued["id"]
    assert client.get(BASE + "/agent/status").json()["documents"] == []


def test_command_queue_is_bounded_and_expired_connections_cannot_pulse(client, factory):
    from app.registry.models import OfficeAgentConnection

    _, edit = editing(client, factory)
    agent = edit["agent"]
    pulse(client, agent)
    for _ in range(20):
        assert command(client, edit).status_code == 200
    assert command(client, edit).status_code == 429
    commands = pulse(client, agent).json()["commands"]
    assert len(commands) == 20
    assert pulse(client, agent, [{"id": commands[0]["id"], "ok": True}]).status_code == 200
    assert command(client, edit).status_code == 200
    with factory.begin() as db:
        db.get(OfficeAgentConnection, UUID(agent["connection_id"])).expires_at = datetime.now(
            UTC
        ) - timedelta(seconds=1)
    assert pulse(client, agent).status_code == 401
