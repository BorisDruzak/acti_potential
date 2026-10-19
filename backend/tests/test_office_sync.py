"""Real PostgreSQL/API acceptance for logical originals and scoped Word writes."""

import hashlib
from datetime import UTC, datetime, timedelta
from io import BytesIO
from urllib.parse import parse_qs, urlsplit
from uuid import UUID
from zipfile import ZipFile

from sqlalchemy import func, select

from app.registry.models import BinaryRevision, DocumentFile, OfficeEditSession, RegistryAccess
from tests.test_documents import headers
from tests.test_office_open import original


def docx(text):
    out = BytesIO()
    with ZipFile(out, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
            + text
            + "</w:t></w:r></w:p></w:body></w:document>",
        )
    return out.getvalue()


def launch(client, file_id):
    response = client.post(
        "/api/v1/office-sync/launch", json={"file_id": file_id}, headers=headers(client)
    )
    assert response.status_code == 200, response.text
    assert client.cookies.get("acti_session") not in response.json()["uri"]
    ticket = parse_qs(urlsplit(response.json()["uri"]).query)["ticket"][0]
    return response.json(), ticket


def exchange(client, ticket):
    return client.post(
        "/api/v1/office-sync/exchange", json={"ticket": ticket, "client_version": "1.0.0"}
    )


def editing(client, factory):
    card = original(client, factory)
    file_id = card["files"][0]["id"]
    _, ticket = launch(client, file_id)
    response = exchange(client, ticket)
    assert response.status_code == 200, response.text
    return card, response.json()


def endpoint(edit, operation):
    return "/api/v1/office-sync/sessions/" + edit["session_id"] + "/" + operation


def credential(edit):
    return {"Authorization": "Bearer " + edit["token"]}


def save(client, edit, body, key="save-1", revision=None, digest=None):
    return client.put(
        endpoint(edit, "save"),
        content=body,
        headers={
            **credential(edit),
            "If-Match": '"' + str(revision or edit["revision"]) + '"',
            "Idempotency-Key": key,
            "X-Content-SHA256": digest or hashlib.sha256(body).hexdigest(),
            "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        },
    )


def test_committed_binary_pointer_keeps_one_attachment_and_immutable_history(client, factory):
    card, edit = editing(client, factory)
    body = docx("server acknowledged changed original")
    download = client.get(endpoint(edit, "download"), headers=credential(edit))
    assert download.status_code == 200 and download.headers["etag"] == '"1"'
    assert download.content == b"owned immutable office bytes"
    ack = save(client, edit, body)
    assert ack.status_code == 200, ack.text
    assert ack.json()["revision"] == 2 and ack.json()["state"] == "saved"
    assert ack.json()["sha256"] == hashlib.sha256(body).hexdigest()
    # Same key/bytes is exactly replayable, unchanged bytes under a new key do not create history.
    assert save(client, edit, body).json()["revision"] == 2
    assert save(client, edit, body, "save-2", revision=2).json()["revision"] == 2
    current = client.get("/api/v1/documents/" + card["id"]).json()
    assert len(current["files"]) == 1
    assert current["revision"] == card["revision"]
    assert current["current_version"] == card["current_version"]
    file = current["files"][0]
    assert file["id"] == edit["file_id"] and file["binary_revision"] == 2
    assert file["current_file_id"] != file["id"]
    assert (
        client.get("/api/v1/files/" + file["id"] + "/download").content
        == b"owned immutable office bytes"
    )
    assert client.get("/api/v1/files/" + file["current_file_id"] + "/download").content == body
    assert client.get("/api/v1/office-sync/files/" + file["id"] + "/download").content == body
    history = client.get("/api/v1/office-sync/files/" + file["id"] + "/history").json()["items"]
    assert [item["revision"] for item in history] == [2, 1]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(BinaryRevision)) == 2
        assert db.scalar(select(func.count()).select_from(DocumentFile)) == 2
    assert (
        client.post(
            endpoint(edit, "heartbeat"), json={"state": "saved"}, headers=credential(edit)
        ).status_code
        == 422
    )
    assert (
        client.get("/api/v1/office-sync/files/" + file["id"] + "/status").json()["state"] == "saved"
    )


def test_ticket_single_use_rotation_conflicts_and_expired_lease(client, factory):
    card = original(client, factory)
    file_id = card["files"][0]["id"]
    pending, ticket = launch(client, file_id)
    result = exchange(client, ticket)
    assert result.status_code == 200, result.text
    edit = result.json()
    assert exchange(client, ticket).status_code == 409
    assert (
        client.post(
            "/api/v1/office-sync/launch", json={"file_id": file_id}, headers=headers(client)
        ).status_code
        == 409
    )
    assert save(client, edit, docx("changed"), revision=5).status_code == 409
    assert save(client, edit, docx("changed"), digest="0" * 64).status_code == 422
    assert save(client, edit, b"not docx").status_code == 422
    renewed = client.post(endpoint(edit, "renew"), headers=credential(edit))
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["token"] != edit["token"]
    assert (
        client.post(
            endpoint(edit, "heartbeat"), json={"state": "editing"}, headers=credential(edit)
        ).status_code
        == 401
    )
    edit = renewed.json()
    with factory.begin() as db:
        db.get(OfficeEditSession, UUID(edit["session_id"])).lease_expires_at = datetime.now(
            UTC
        ) - timedelta(seconds=1)
    assert save(client, edit, docx("offline stale bytes")).status_code == 409
    assert (
        client.get("/api/v1/office-sync/files/" + file_id + "/status").json()["state"] == "offline"
    )
    _, new_ticket = launch(client, file_id)
    next_edit = exchange(client, new_ticket).json()
    assert save(client, next_edit, docx("new owner")).status_code == 200
    assert save(client, edit, docx("old owner must not overwrite")).status_code == 409


def test_every_operation_revalidates_acl_and_browser_logout(client, factory):
    card, edit = editing(client, factory)
    with factory.begin() as db:
        access = db.get(
            RegistryAccess, (UUID(card["registry_id"]), UUID(card["files"][0]["created_by"]))
        )
        access.permissions = ["read", "update", "download"]
    assert save(client, edit, docx("denied")).status_code == 403
    assert client.get(endpoint(edit, "download"), headers=credential(edit)).status_code == 403
    assert client.post(endpoint(edit, "renew"), headers=credential(edit)).status_code == 403
    with factory.begin() as db:
        db.get(
            RegistryAccess, (UUID(card["registry_id"]), UUID(card["files"][0]["created_by"]))
        ).permissions = ["read", "update", "upload", "download"]
    assert client.post("/api/v1/auth/logout", headers=headers(client)).status_code == 204
    assert save(client, edit, docx("revoked")).status_code == 401
    assert (
        client.post(
            endpoint(edit, "heartbeat"), json={"state": "editing"}, headers=credential(edit)
        ).status_code
        == 401
    )


def test_official_policy_and_csrf_cannot_be_bypassed(client, factory):
    from app.registry.models import Document, MetadataRevision

    card = original(client, factory)
    file_id = card["files"][0]["id"]
    assert client.post("/api/v1/office-sync/launch", json={"file_id": file_id}).status_code == 403
    with factory.begin() as db:
        db.get(Document, UUID(card["id"])).reg_number = "registered-immutable"
    status = client.get("/api/v1/office-sync/files/" + file_id + "/status").json()
    assert status["can_edit"] is False and status["reason"]
    assert (
        client.post(
            "/api/v1/office-sync/launch", json={"file_id": file_id}, headers=headers(client)
        ).status_code
        == 403
    )
    with factory.begin() as db:
        db.get(Document, UUID(card["id"])).reg_number = None
        db.add(
            MetadataRevision(
                document_id=UUID(card["id"]),
                revision=99,
                values={},
                card_state="published",
                publication_targets=["internal"],
                created_by=UUID(card["files"][0]["created_by"]),
            )
        )
    assert (
        client.post(
            "/api/v1/office-sync/launch", json={"file_id": file_id}, headers=headers(client)
        ).status_code
        == 403
    )


def test_finish_releases_lease_and_ticket_expiry(client, factory):
    card, edit = editing(client, factory)
    result = client.post(endpoint(edit, "finish"), headers=credential(edit))
    assert result.status_code == 200 and result.json()["state"] == "finished"
    assert save(client, edit, docx("finished")).status_code == 401
    pending, ticket = launch(client, edit["file_id"])
    with factory.begin() as db:
        db.get(OfficeEditSession, UUID(pending["session_id"])).ticket_expires_at = datetime.now(
            UTC
        ) - timedelta(seconds=1)
    assert exchange(client, ticket).status_code == 409


def test_changed_idempotency_bytes_and_size_reject_without_history(client, factory):
    _, edit = editing(client, factory)
    assert save(client, edit, docx("one")).status_code == 200
    assert save(client, edit, docx("different"), key="save-1").status_code == 409
    assert (
        save(client, edit, b"x" * (25 * 1024 * 1024 + 1), key="large", revision=2).status_code
        == 413
    )
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(BinaryRevision)) == 2


def test_manual_versions_are_targeted_idempotent_and_do_not_replace_other_originals(
    client, factory
):
    card = original(client, factory)
    file_id = card["files"][0]["id"]
    url = "/api/v1/office-sync/files/" + file_id + "/content"
    body = docx("manual new binary edition")
    save_headers = {
        **headers(client),
        "If-Match": '"1"',
        "Idempotency-Key": "manual-1",
        "X-Content-SHA256": hashlib.sha256(body).hexdigest(),
    }
    response = client.put(url, content=body, headers=save_headers)
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 2 and response.json()["state"] == "saved"
    assert client.put(url, content=body, headers=save_headers).json()["revision"] == 2
    assert (
        client.put(
            url, content=body, headers={**save_headers, "Idempotency-Key": "stale"}
        ).status_code
        == 409
    )
    current = client.get("/api/v1/documents/" + card["id"]).json()
    assert current["revision"] == card["revision"] and len(current["files"]) == 1
    other = client.post(
        "/api/v1/documents/" + card["id"] + "/files",
        params={"filename": "original.docx", "revision": current["revision"]},
        content=docx("independent identical filename"),
        headers=headers(client),
    )
    assert other.status_code == 201 and len(other.json()["files"]) == 2
    _, ticket = launch(client, file_id)
    edit = exchange(client, ticket).json()
    assert client.put(url, content=body, headers=save_headers).status_code == 409
    assert client.post(endpoint(edit, "finish"), headers=credential(edit)).status_code == 200


def test_idempotent_ack_remains_exact_after_subsequent_save(client, factory):
    _, edit = editing(client, factory)
    first, second = docx("first server edition"), docx("second server edition")
    assert save(client, edit, first).json()["revision"] == 2
    assert save(client, edit, second, key="second", revision=2).json()["revision"] == 3
    response = save(client, edit, first)
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 2
    assert response.json()["sha256"] == hashlib.sha256(first).hexdigest()
    assert client.get(endpoint(edit, "download"), headers=credential(edit)).content == second
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(BinaryRevision)) == 3


def test_storage_rollback_does_not_publish_or_leak_uncommitted_binary(client, factory, monkeypatch):
    from pathlib import Path

    from app.registry import office_sync_api

    _, edit = editing(client, factory)
    storage = Path(client.app.state.settings.storage_root)
    before = {p for p in storage.rglob("*") if p.is_file()}

    def fail_queue(*args, **kwargs):
        raise RuntimeError("synthetic transaction failure")

    monkeypatch.setattr(office_sync_api, "queue_file", fail_queue)
    response = save(client, edit, docx("failed transaction"))
    assert response.status_code == 500
    assert {p for p in storage.rglob("*") if p.is_file()} == before
    status = client.get("/api/v1/office-sync/files/" + edit["file_id"] + "/status").json()
    assert status["revision"] == 1 and status["last_saved_at"] is None
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(BinaryRevision)) == 1
        assert db.scalar(select(func.count()).select_from(DocumentFile)) == 1


def test_concurrent_exchange_and_save_are_serialized_by_original(client, factory):
    from concurrent.futures import ThreadPoolExecutor

    card = original(client, factory)
    _, ticket = launch(client, card["files"][0]["id"])
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(lambda _: exchange(client, ticket), range(2)))
    assert sorted(r.status_code for r in replies) == [200, 409]
    edit = next(r.json() for r in replies if r.status_code == 200)
    body = docx("concurrent same bytes")
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(lambda _: save(client, edit, body), range(2)))
    assert [r.status_code for r in replies] == [200, 200]
    assert [r.json()["revision"] for r in replies] == [2, 2]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(BinaryRevision)) == 2


def test_current_search_uses_pointer_and_old_extractions_remain_historical(client, factory):
    from app.content.worker import process_extractions
    from tests.test_search_engine_v2 import search

    card, edit = editing(client, factory)
    old_body, new_body = docx("quartzcelestialword"), docx("nebulaorchardword")
    assert save(client, edit, old_body).status_code == 200
    process_extractions(factory, client.app.state.settings.storage_root)
    assert (
        search(client, headers(client), query="quartzcelestialword", version_scope="current")[
            "total"
        ]
        == 1
    )
    assert save(client, edit, new_body, key="new", revision=2).status_code == 200
    # Old indexed text is retired immediately, even before new extraction completes.
    assert (
        search(client, headers(client), query="quartzcelestialword", version_scope="current")[
            "total"
        ]
        == 0
    )
    assert (
        search(client, headers(client), query="quartzcelestialword", version_scope="all")["total"]
        == 1
    )
    process_extractions(factory, client.app.state.settings.storage_root)
    assert (
        search(client, headers(client), query="nebulaorchardword", version_scope="current")["total"]
        == 1
    )
    content = client.get(
        "/api/v1/documents/" + card["id"] + "/content", params={"file_id": edit["file_id"]}
    ).json()
    assert (
        content["total"] == 1
        and content["items"][0]["sha256"] == hashlib.sha256(new_body).hexdigest()
    )


def test_blocked_user_deleted_document_field_acl_and_viewer_cannot_write(client, factory):
    from app.identity.models import Role, User, UserRole
    from app.registry.models import Document, FormSchema

    card, edit = editing(client, factory)
    body = docx("denied changes")
    actor_id = UUID(card["files"][0]["created_by"])
    with factory.begin() as db:
        db.get(User, actor_id).active = False
    assert save(client, edit, body).status_code == 401
    with factory.begin() as db:
        db.get(User, actor_id).active = True
        db.get(Document, UUID(card["id"])).deleted_at = datetime.now(UTC)
    assert save(client, edit, body).status_code == 404
    with factory.begin() as db:
        db.get(Document, UUID(card["id"])).deleted_at = None
        file = db.get(DocumentFile, UUID(edit["file_id"]))
        file.field_key = "restricted_original"
        document = db.get(Document, UUID(card["id"]))
        schema = db.get(FormSchema, document.schema_version_id)
        data = dict(schema.schema_data)
        data["sections"] = [
            *data["sections"],
            {
                "key": "sync_test",
                "label": "Sync",
                "fields": [
                    {
                        "key": "restricted_original",
                        "label": "Restricted",
                        "type": "file",
                        "write_roles": ["editor"],
                    }
                ],
            },
        ]
        replacement = FormSchema(
            document_type_id=schema.document_type_id,
            version=schema.version + 1,
            state="published",
            schema_data=data,
        )
        db.add(replacement)
        db.flush()
        document.schema_version_id = replacement.id
        document.custom_data = {**document.custom_data, "restricted_original": [edit["file_id"]]}
    # Admin without the field's editor role is still forbidden.
    assert save(client, edit, body).status_code == 403
    with factory.begin() as db:
        file = db.get(DocumentFile, UUID(edit["file_id"]))
        file.field_key = None
        from sqlalchemy import delete

        db.execute(delete(UserRole).where(UserRole.user_id == actor_id))
        viewer = db.scalar(select(Role).where(Role.code == "viewer"))
        db.add(UserRole(user_id=actor_id, role_id=viewer.id))
        db.get(Document, UUID(card["id"])).card_state = "published"
        db.get(Document, UUID(card["id"])).publication_targets = ["shared"]
    assert save(client, edit, body).status_code == 403
    state = client.get("/api/v1/office-sync/files/" + edit["file_id"] + "/status").json()
    assert state["can_edit"] is False


def test_compatible_client_patch_versions_and_unsupported_major(client, factory):
    card = original(client, factory)
    _, ticket = launch(client, card["files"][0]["id"])
    assert (
        client.post(
            "/api/v1/office-sync/exchange", json={"ticket": ticket, "client_version": "2.0.0"}
        ).status_code
        == 422
    )
    reply = client.post(
        "/api/v1/office-sync/exchange", json={"ticket": ticket, "client_version": "1.0.1"}
    )
    assert reply.status_code == 200, reply.text


def test_additive_migration_backfills_legacy_ids_without_rewriting_originals(client, factory):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from app.registry.models import Document, LogicalOriginal

    card = original(client, factory)
    identity = UUID(card["files"][0]["id"])
    with factory() as db:
        before = db.get(DocumentFile, identity)
        fingerprint = (before.storage_key, before.sha256, before.size, before.version_id)
        values = dict(db.get(Document, UUID(card["id"])).custom_data)
    engine = factory.kw["bind"]
    with engine.begin() as connection:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        command.downgrade(config, "0019_number_year_type")
        command.upgrade(config, "head")
    with factory() as db:
        file = db.get(DocumentFile, identity)
        current = db.get(LogicalOriginal, identity)
        assert current.current_file_id == identity and current.revision == 1
        assert (file.storage_key, file.sha256, file.size, file.version_id) == fingerprint
        assert db.get(Document, UUID(card["id"])).custom_data == values
