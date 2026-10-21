"""An expired lease can resume only against its unchanged acknowledged original."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select

from app.identity.models import AuthSession
from app.platform.models import AuditEvent
from app.registry.models import Document, OfficeEditSession, RegistryAccess
from tests.test_office_sync import credential, docx, editing, endpoint, exchange, launch, save


def expire(factory, edit):
    with factory.begin() as db:
        db.get(OfficeEditSession, UUID(edit["session_id"])).lease_expires_at = (
            datetime.now(UTC) - timedelta(seconds=1)
        )


def resume(client, edit, **values):
    return client.post(endpoint(edit, "resume"), headers=credential(edit), json={
        "revision": edit["revision"], "sha256": edit["sha256"], **values,
    })


def test_resume_unchanged_original_is_idempotent_and_keeps_scoped_token(client, factory):
    _, edit = editing(client, factory)
    expire(factory, edit)
    assert save(client, edit, docx("still forbidden before explicit resume")).status_code == 409
    assert client.post(endpoint(edit, "renew"), headers=credential(edit)).status_code == 409
    result = resume(client, edit)
    assert result.status_code == 200, result.text
    assert "token" not in result.json()
    assert result.json()["revision"] == edit["revision"]
    assert result.json()["sha256"] == edit["sha256"]
    assert datetime.fromisoformat(result.json()["lease_expires_at"]) > datetime.now(UTC)
    assert resume(client, edit).status_code == 200
    with factory() as db:
        events = list(db.scalars(select(AuditEvent).where(
            AuditEvent.action == "document.sync.lease_resumed")))
        assert len(events) == 1
    assert save(client, edit, docx("pending local bytes after restart")).status_code == 200


@pytest.mark.parametrize("owner", ["reserved", "exchanged"])
def test_resume_never_takes_another_live_lease_even_without_a_save(client, factory, owner):
    _, edit = editing(client, factory)
    expire(factory, edit)
    newer, ticket = launch(client, edit["file_id"])
    if owner == "exchanged":
        newer = exchange(client, ticket).json()
    with factory() as db:
        before = db.get(OfficeEditSession, UUID(newer["session_id"])).lease_expires_at
    denied = resume(client, edit)
    assert denied.status_code == 409, denied.text
    with factory() as db:
        assert db.get(OfficeEditSession, UUID(newer["session_id"])).lease_expires_at == before


def test_resume_refuses_changed_original_after_another_editor_finishes(client, factory):
    _, edit = editing(client, factory)
    expire(factory, edit)
    _, ticket = launch(client, edit["file_id"])
    newer = exchange(client, ticket).json()
    assert save(client, newer, docx("other editor's immutable revision")).status_code == 200
    assert client.post(endpoint(newer, "finish"), headers=credential(newer)).status_code == 200
    assert resume(client, edit).status_code == 409
    assert resume(client, edit, revision=2).status_code == 409


@pytest.mark.parametrize("mutation,expected", [
    ("expired_token", 401), ("expired_auth", 401), ("finished", 401),
    ("revoked_acl", 403), ("deleted_document", 404), ("wrong_token", 401),
])
def test_resume_preserves_auth_expiry_finish_and_current_acl(client, factory, mutation, expected):
    card, edit = editing(client, factory)
    expire(factory, edit)
    with factory.begin() as db:
        row = db.get(OfficeEditSession, UUID(edit["session_id"]))
        past = datetime.now(UTC) - timedelta(seconds=1)
        if mutation == "expired_token":
            row.expires_at = past
        elif mutation == "expired_auth":
            db.get(AuthSession, row.auth_session_id).expires_at = past
        elif mutation == "finished":
            row.finished_at = past
        elif mutation == "revoked_acl":
            db.get(RegistryAccess, (UUID(card["registry_id"]), row.user_id)).permissions = [
                "read", "download", "update"]
        elif mutation == "deleted_document":
            db.get(Document, UUID(card["id"])).deleted_at = past
        else:
            edit["token"] = "x" * 43
    assert resume(client, edit).status_code == expected


@pytest.mark.parametrize("values", [
    {"revision": 0}, {"revision": True}, {"sha256": "f" * 63}, {"extra": True},
])
def test_resume_requires_strict_acknowledged_revision_and_hash(client, factory, values):
    _, edit = editing(client, factory)
    expire(factory, edit)
    assert resume(client, edit, **values).status_code == 422


@pytest.mark.parametrize("mutation", [None, "key", "hash", "revision", "other_owner", "newer_revision"])
def test_resume_reconciles_only_own_committed_save_receipt(client, factory, mutation):
    _, edit = editing(client, factory)
    body = docx("reply lost after a committed save")
    digest = hashlib.sha256(body).hexdigest()
    assert save(client, edit, body, key="uncertain-save").status_code == 200
    expire(factory, edit)
    proof = {"key": "uncertain-save", "revision": 1, "sha256": digest}
    if mutation in {"other_owner", "newer_revision"}:
        _, ticket = launch(client, edit["file_id"])
        newer = exchange(client, ticket).json()
        if mutation == "newer_revision":
            assert save(client, newer, docx("later version"), key="later").status_code == 200
            assert client.post(endpoint(newer, "finish"), headers=credential(newer)).status_code == 200
    elif mutation == "key":
        proof["key"] = "another-session-key"
    elif mutation == "hash":
        proof["sha256"] = "f" * 64
    elif mutation == "revision":
        proof["revision"] = 2
    result = resume(client, edit, pending=proof)
    if mutation is not None:
        assert result.status_code == 409, result.text
        return
    assert result.status_code == 200, result.text
    assert result.json()["revision"] == 2 and result.json()["sha256"] == digest
    assert resume(client, edit, pending=proof).status_code == 200
    assert save(client, edit, body, key="uncertain-save").json()["revision"] == 2
    history = client.get("/api/v1/office-sync/files/" + edit["file_id"] + "/history").json()
    assert len(history["items"]) == 2
