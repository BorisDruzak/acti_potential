"""Scoped Word sessions. Every operation revalidates the originating browser and ACL.

Authorized saves append immutable binary revisions to the same logical original,
including registered, published and imported documents. Prior bytes and metadata
remain unchanged; every new revision is audited.
"""

import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from tempfile import SpooledTemporaryFile
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import event, func, or_, select
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import COOKIE_NAME, current_actor, db_session
from app.api.errors import fail
from app.content.indexing import queue_file
from app.identity.models import AuthSession, User
from app.identity.sessions import resolve_actor_by_hash, token_digest
from app.registry.documents import (
    file_visible,
    get_document,
    get_schema,
    raw_values,
    resolve_file_field,
)
from app.registry.events import emit
from app.registry.file_api import UPLOAD_LIMIT
from app.registry.models import (
    BinaryRevision,
    DocumentFile,
    LogicalOriginal,
    OfficeAgentCommand,
    OfficeAgentConnection,
    OfficeAgentTicket,
    OfficeEditSession,
    OfficeSaveReceipt,
)
from app.registry.policy import document_permissions, field_writable
from app.storage.local import LocalFileStorage

router = APIRouter(prefix="/office-sync")
client_router = APIRouter(prefix="/office-sync")
Db = Annotated[object, Depends(db_session, scope="function")]
Actor = Annotated[object, Depends(current_actor)]
LEASE_SECONDS = 90
TOKEN_SECONDS = 900
WORD_CLOSE_MESSAGE = "Сохраните и закройте документ в Word"
MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
SOURCE_MIMES = {
    "doc": "application/msword",
    "docx": MIME,
    "docm": "application/vnd.ms-word.document.macroEnabled.12",
    "dot": "application/msword",
    "dotx": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
    "dotm": "application/vnd.ms-word.template.macroEnabled.12",
    "rtf": "application/rtf",
    "odt": "application/vnd.oasis.opendocument.text",
}


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Launch(Input):
    file_id: UUID


class Exchange(Input):
    ticket: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$", repr=False)
    client_version: str = Field(max_length=32, pattern=r"^1\.[0-9]{1,5}\.[0-9]{1,5}$")


class Heartbeat(Input):
    state: Literal["opening", "editing", "local_saved", "syncing", "offline", "conflict", "error"]
    document_open: bool | None = None
    pending_uploads: int | None = Field(default=None, ge=0, le=2, strict=True)
    error_code: Literal["network", "conflict", "reauth", "quota", "file_busy", "word_missing", "local_io", "server_rejected"] | None = None


class Recover(Input):
    command_id: UUID
    local_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_closed: bool = Field(strict=True)


def ensure_original(session, file):
    """Caller holds document lock when creating; preserve every legacy ID separately."""
    row = session.get(LogicalOriginal, file.id)
    if row is None:
        # A revision's immutable ID must never silently become another original.
        if session.scalar(select(BinaryRevision.file_id).where(BinaryRevision.file_id == file.id)):
            fail(409, "historical_original", "Выберите актуальный оригинал")
        row = LogicalOriginal(
            id=file.id, document_id=file.document_id, current_file_id=file.id, revision=1
        )
        session.add(row)
        session.flush()
        session.add(BinaryRevision(original_id=file.id, revision=1, file_id=file.id))
        session.flush()
    return row


def authorized(session, actor, file_id, *, write=False, lock=False, mode=None):
    anchor = session.get(DocumentFile, file_id)
    if anchor is None:
        fail(404, "not_found", "Файл не найден")
    document = get_document(session, actor, anchor.document_id, "download", lock=lock, mode=mode)
    schema, values = get_schema(session, document), None
    values = raw_values(document, schema)
    if not file_visible(schema, actor, anchor, values, mode):
        fail(404, "not_found", "Файл не найден")
    if write:
        if not {"read", "update", "upload", "download"} <= document_permissions(
            session, actor, document
        ):
            fail(403, "forbidden", "Нужны права изменения, загрузки и скачивания")
        if (
            actor.must_change_password
            or actor.scoped_operations is not None
            or actor.scoped_registry_ids is not None
        ):
            fail(403, "interactive_session_required", "Нужна действующая пользовательская сессия")
        if PurePath(anchor.name).suffix.lower().removeprefix(".") not in SOURCE_MIMES:
            fail(422, "office_format", "Редактирование поддерживает DOC, DOCX, DOCM, DOT, DOTX, DOTM, RTF и ODT")
        if anchor.field_key:
            field, parent, index = resolve_file_field(schema, anchor.field_key, values)
            if not field or not field_writable(field, actor):
                fail(403, "field_forbidden", "Изменение поля не разрешено")
            target = values[parent][index] if parent else values
            if str(anchor.id) not in (target.get(field["key"]) or []):
                fail(403, "field_detached", "Файл больше не выбран в поле документа")
            if parent:
                group = next(
                    f for s in schema["sections"] for f in s["fields"] if f["key"] == parent
                )
                if not field_writable(group, actor):
                    fail(403, "field_forbidden", "Изменение группы не разрешено")
    original = session.get(LogicalOriginal, file_id)
    if original is None:
        if lock:
            original = ensure_original(session, anchor)
        elif session.scalar(
            select(BinaryRevision.file_id).where(BinaryRevision.file_id == file_id)
        ):
            fail(409, "historical_original", "Выберите актуальный оригинал")
    if lock:
        original = session.scalar(
            select(LogicalOriginal)
            .where(LogicalOriginal.id == file_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    return document, anchor, original


def current_file(session, anchor, original):
    return session.get(DocumentFile, original.current_file_id) if original else anchor


def source_format(file):
    return PurePath(file.name).suffix.lower().removeprefix(".")


def docx_filename(name):
    # Replace the extension, never append it or interpret the name as a storage path.
    return name.rsplit(".", 1)[0] + ".docx"


def status_payload(session, row, original):
    file = session.get(DocumentFile, original.current_file_id)
    return {
        "capabilities": ["queue-telemetry-v1", "recovery-v1"],
        "session_id": row.id,
        "file_id": original.id,
        "filename": docx_filename(file.name) if source_format(file) != "docx" else file.name,
        "source_format": source_format(file),
        "revision": original.revision,
        "sha256": file.sha256,
        "expires_at": row.expires_at,
        "lease_expires_at": row.lease_expires_at,
        "state": row.state,
        "last_seen_at": row.last_seen_at,
        "last_saved_at": row.last_saved_at,
        "document_open": row.document_open,
        "opened_at": row.opened_at,
        "closed_at": row.closed_at,
        "connection_id": row.connection_id,
    }


def active_session(session, original_id, now):
    return session.scalar(
        select(OfficeEditSession)
        .where(
            OfficeEditSession.original_id == original_id,
            OfficeEditSession.finished_at.is_(None),
            OfficeEditSession.lease_expires_at > now,
            OfficeEditSession.expires_at > now,
        )
        .order_by(OfficeEditSession.created_at.desc())
        .limit(1)
    )


def verify_browser(session, row, now):
    auth = session.get(AuthSession, row.auth_session_id)
    if auth is None or auth.user_id != row.user_id:
        fail(401, "sync_access_revoked", "Повторно войдите в ACTI; локальная копия сохранена")
    return resolve_actor_by_hash(session, auth.token_hash, now)


def client_context(session, request, identity, *, allow_expired_lease=False, allow_finished=False, lock=True):
    row = session.get(OfficeEditSession, identity)
    bearer = request.headers.get("Authorization", "")
    token = bearer[7:] if bearer.startswith("Bearer ") else ""
    now = datetime.now(UTC)
    if (
        row is None
        or len(token) != 43
        or not row.token_hash
        or not secrets.compare_digest(row.token_hash, token_digest(token))
    ):
        fail(401, "sync_token_invalid", "Повторно откройте документ из ACTI")
    actor = verify_browser(session, row, now)
    _, anchor, original = authorized(session, actor, row.original_id, write=True, lock=lock)
    if lock:
        row = session.scalar(
            select(OfficeEditSession)
            .where(OfficeEditSession.id == identity)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    # Recheck after obtaining locks: concurrent rotation, finish or expiry is final.
    if (
        not row.token_hash
        or not secrets.compare_digest(row.token_hash, token_digest(token))
        or (row.finished_at and not allow_finished)
        or row.expires_at <= now
    ):
        fail(401, "sync_access_expired", "Срок доступа истёк; локальная копия сохранена")
    if not allow_expired_lease and row.lease_expires_at <= now:
        fail(
            409,
            "lease_expired",
            "Срок блокировки истёк. Повторно откройте файл из ACTI; локальная копия сохранена",
        )
    return row, actor, anchor, original, now


@router.post("/launch")
def launch(data: Launch, request: Request, session: Db, actor: Actor):
    _, anchor, original = authorized(session, actor, data.file_id, write=True, lock=True)
    origin = urlsplit(request.app.state.settings.origin)
    if origin.scheme != "https" or not origin.netloc or origin.username:
        fail(503, "office_https_required", "Для ACTI Sync требуется HTTPS")
    now = datetime.now(UTC)
    existing = active_session(session, original.id, now)
    if existing:
        fail(
            409,
            "original_locked",
            "Оригинал уже открыт в ACTI Sync. Продолжите работу с открытой копией",
        )
    auth = session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == token_digest(request.cookies[COOKIE_NAME])
        )
    )
    token = secrets.token_urlsafe(32)
    row = OfficeEditSession(
        original_id=original.id,
        auth_session_id=auth.id,
        user_id=actor.id,
        ticket_hash=token_digest(token),
        ticket_expires_at=now + timedelta(seconds=60),
        expires_at=min(auth.expires_at, now + timedelta(seconds=TOKEN_SECONDS)),
        lease_expires_at=now + timedelta(seconds=60),
        state="opening",
    )
    session.add(row)
    session.flush()
    emit(session, request, actor, "document.sync.launched", "document_file", original.id)
    return {
        "uri": "acti-sync://launch?ticket=" + token,
        "session_id": row.id,
        "expires_at": row.ticket_expires_at,
    }


@client_router.post("/exchange")
def exchange(data: Exchange, request: Request, session: Db):
    # Lock order is document -> original -> session everywhere to avoid deadlocks.
    probe = session.scalar(
        select(OfficeEditSession).where(OfficeEditSession.ticket_hash == token_digest(data.ticket))
    )
    now = datetime.now(UTC)
    if probe is None:
        fail(401, "sync_ticket_invalid", "Ссылка запуска недействительна")
    actor = verify_browser(session, probe, now)
    _, _, original = authorized(session, actor, probe.original_id, write=True, lock=True)
    row = session.scalar(
        select(OfficeEditSession)
        .where(OfficeEditSession.id == probe.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        row.exchanged_at
        or row.finished_at
        or row.ticket_expires_at <= now
        or row.lease_expires_at <= now
    ):
        fail(409, "sync_ticket_consumed", "Ссылка запуска истекла или уже использована")
    if source_format(session.get(DocumentFile, original.current_file_id)) != "docx" and tuple(
        int(part) for part in data.client_version.split(".")
    ) < (1, 2, 0):
        fail(422, "client_upgrade_required", "Для преобразования в DOCX обновите ACTI Sync до 1.2.0")
    row.exchanged_at, row.last_seen_at = now, now
    row.lease_expires_at, row.state = now + timedelta(seconds=LEASE_SECONDS), "opening"
    token = secrets.token_urlsafe(32)
    row.token_hash = token_digest(token)
    agent = issue_agent_connection(session, row, data.client_version, now)
    row.connection_id = agent["connection_id"]
    return {**status_payload(session, row, original), "token": token, "agent": agent}


@client_router.post("/sessions/{identity}/heartbeat")
def heartbeat(identity: UUID, data: Heartbeat, request: Request, session: Db):
    row, _, _, original, now = client_context(session, request, identity)
    row.last_seen_at, row.lease_expires_at = now, now + timedelta(seconds=LEASE_SECONDS)
    row.pending_uploads, row.error_code = data.pending_uploads, data.error_code
    row.telemetry_seen_at = now if data.pending_uploads is not None else None
    if data.document_open is True and row.document_open is not True:
        row.document_open, row.opened_at, row.closed_at = True, now, None
    elif data.document_open is False and row.document_open is True:
        row.document_open, row.closed_at = False, now
    # The client cannot claim a server acknowledgement through heartbeat.
    # Preserve saved only if it reports an unchanged editing state.
    if not (row.state == "saved" and data.state == "editing"):
        row.state = data.state
    return status_payload(session, row, original)


@client_router.post("/sessions/{identity}/renew")
def renew(identity: UUID, request: Request, session: Db):
    row, _, _, original, now = client_context(session, request, identity)
    auth = session.get(AuthSession, row.auth_session_id)
    token = secrets.token_urlsafe(32)
    row.token_hash = token_digest(token)
    row.expires_at = min(auth.expires_at, now + timedelta(seconds=TOKEN_SECONDS))
    row.last_seen_at, row.lease_expires_at = now, now + timedelta(seconds=LEASE_SECONDS)
    return {**status_payload(session, row, original), "token": token}


def stream_original(request, session, file, revision):
    storage = LocalFileStorage(request.app.state.settings.storage_root)
    try:
        stored = storage.stat(file.storage_key)
    except (OSError, ValueError):
        fail(404, "file_missing", "Оригинал временно недоступен")
    if (stored.size, stored.sha256) != (file.size, file.sha256):
        fail(409, "file_integrity", "Проверка целостности не пройдена")

    def chunks():
        with storage.open_stream(file.storage_key) as source:
            while chunk := source.read(1024 * 1024):
                yield chunk

    return StreamingResponse(
        chunks(),
        media_type=SOURCE_MIMES.get(source_format(file), "application/octet-stream"),
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(file.name, safe=""),
            "Content-Length": str(file.size),
            "ETag": f'"{revision}"',
            "X-Content-SHA256": file.sha256,
            "Cache-Control": "no-store",
        },
    )


@client_router.get("/sessions/{identity}/download")
def session_download(identity: UUID, request: Request, session: Db):
    _, _, anchor, original, _ = client_context(session, request, identity)
    return stream_original(
        request, session, current_file(session, anchor, original), original.revision
    )


def validate_docx(source):
    try:
        with ZipFile(source) as archive:
            entries = archive.infolist()
            if (
                len(entries) > 10000
                or sum(i.file_size for i in entries) > 200 * 1024 * 1024
                or not {"[Content_Types].xml", "word/document.xml"} <= set(archive.namelist())
                or any(
                    i.flag_bits & 1 or i.filename.lower().endswith("vbaproject.bin")
                    for i in entries
                )
            ):
                fail(422, "invalid_docx", "Нужен корректный DOCX без макросов и шифрования")
            if archive.testzip() is not None:
                fail(422, "invalid_docx", "DOCX повреждён")
    except (BadZipFile, OSError, RuntimeError):
        fail(422, "invalid_docx", "DOCX повреждён или зашифрован")
    source.seek(0)


@client_router.put("/sessions/{identity}/save")
async def save(identity: UUID, request: Request, session: Db):
    await run_in_threadpool(client_context, session, request, identity, lock=False)
    return await save_content(identity, request, session, client_context)


async def save_content(identity, request, session, context):
    match, key, digest = (
        request.headers.get(name, "")
        for name in ("If-Match", "Idempotency-Key", "X-Content-SHA256")
    )
    if not re.fullmatch(r'"[1-9][0-9]{0,9}"', match):
        fail(428, "revision_required", "Нужна версия содержимого If-Match")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", key) or not re.fullmatch(
        r"[0-9a-f]{64}", digest
    ):
        fail(422, "save_headers_invalid", "Нужны ключ повторной отправки и SHA256")
    expected = int(match[1:-1])
    # Do not hold SQL write locks while waiting for upload bytes.
    storage = LocalFileStorage(request.app.state.settings.storage_root)
    size = 0
    with SpooledTemporaryFile(max_size=1024 * 1024, dir=storage.root) as source:
        import hashlib

        hasher = hashlib.sha256()
        async for chunk in request.stream():
            size += len(chunk)
            if size > UPLOAD_LIMIT:
                fail(413, "file_too_large", "Размер файла превышает 25 МиБ")
            source.write(chunk)
            hasher.update(chunk)
        if not size or not secrets.compare_digest(hasher.hexdigest(), digest):
            fail(422, "content_hash_mismatch", "Содержимое не соответствует SHA256")
        return await run_in_threadpool(
            commit_save, source, expected, key, digest, identity, request, session, context
        )


def commit_save(source, expected, key, digest, identity, request, session, context):
    storage = LocalFileStorage(request.app.state.settings.storage_root)
    session.expire_all()
    row, actor, anchor, original, now = context(session, request, identity)
    identity = row.id
    receipt = session.get(OfficeSaveReceipt, (identity, key))
    if receipt:
        if receipt.sha256 != digest or receipt.expected_revision != expected:
            fail(409, "idempotency_conflict", "Ключ повторной отправки уже использован")
        # Return the original commit acknowledgement even if another save followed it.
        return {
            **status_payload(session, row, original),
            "revision": receipt.revision,
            "sha256": receipt.sha256,
            "state": "saved",
        }
    if expected != original.revision:
        fail(
            409,
            "binary_revision_conflict",
            "Оригинал изменён. Локальная копия сохранена; требуется действие",
        )
    current = current_file(session, anchor, original)
    if current.sha256 != digest or source_format(current) != "docx":
        source.seek(0)
        validate_docx(source)
    if current.sha256 != digest:
        stored = storage.put(uuid4().hex, source)

        def cleanup(_session):
            try:
                storage.delete_if_allowed(stored.storage_key, allowed=True)
            except OSError:
                pass

        event.listen(session, "after_rollback", cleanup, once=True)
        file = DocumentFile(
            document_id=anchor.document_id,
            version_id=anchor.version_id,
            field_key=anchor.field_key,
            name=docx_filename(current.name) if source_format(current) != "docx" else current.name,
            size=stored.size,
            mime=MIME,
            sha256=stored.sha256,
            storage_key=stored.storage_key,
            created_by=actor.id,
        )
        session.add(file)
        session.flush()
        original.revision += 1
        original.current_file_id = file.id
        session.add(
            BinaryRevision(original_id=original.id, revision=original.revision, file_id=file.id)
        )
        queue_file(session, file)
        emit(
            session,
            request,
            actor,
            "document.binary.saved",
            "document",
            anchor.document_id,
            {
                "original_id": str(original.id),
                "file_id": str(file.id),
                "version": original.revision,
                **(
                    {
                        "previous_file_id": str(current.id),
                        "from_format": source_format(current),
                        "to_format": "docx",
                    }
                    if source_format(current) != "docx"
                    else {}
                ),
            },
        )
    row.state, row.last_saved_at, row.last_seen_at = "saved", now, now
    row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    session.add(
        OfficeSaveReceipt(
            session_id=identity,
            idempotency_key=key,
            expected_revision=expected,
            revision=original.revision,
            sha256=digest,
        )
    )
    session.flush()
    return status_payload(session, row, original)


@router.put("/files/{file_id}/content")
async def manual_version(file_id: UUID, request: Request, session: Db, actor: Actor):
    """An explicit new-version action; the URL, never a filename, selects identity."""
    authorized(session, actor, file_id, write=True)

    def manual_context(db, req, identity):
        moment = datetime.now(UTC)
        browser_actor = resolve_actor_by_hash(db, token_digest(req.cookies[COOKIE_NAME]), moment)
        _, initial, current = authorized(db, browser_actor, file_id, write=True, lock=True)
        if active_session(db, file_id, moment):
            fail(409, "original_locked", "Сначала завершите редактирование в ACTI Sync")
        auth = db.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == token_digest(req.cookies[COOKIE_NAME])
            )
        )
        namespace = token_digest("manual:" + str(auth.id) + ":" + str(current.id))
        record = db.scalar(
            select(OfficeEditSession).where(OfficeEditSession.ticket_hash == namespace)
        )
        if record is None:
            record = OfficeEditSession(
                original_id=current.id,
                auth_session_id=auth.id,
                user_id=browser_actor.id,
                ticket_hash=namespace,
                ticket_expires_at=moment,
                exchanged_at=moment,
                expires_at=auth.expires_at,
                lease_expires_at=moment,
                state="finished",
                finished_at=moment,
            )
            db.add(record)
            db.flush()
        return record, browser_actor, initial, current, moment

    result = await save_content(None, request, session, manual_context)
    row = session.get(OfficeEditSession, result["session_id"])
    row.finished_at = datetime.now(UTC)
    row.lease_expires_at = row.finished_at
    row.state = "finished"
    return result


@client_router.post("/sessions/{identity}/finish")
def finish(identity: UUID, request: Request, session: Db):
    row, actor, anchor, original, now = client_context(
        session, request, identity, allow_expired_lease=True
    )
    row.finished_at, row.state, row.last_seen_at = now, "finished", now
    row.lease_expires_at = now
    emit(session, request, actor, "document.sync.finished", "document_file", anchor.id)
    return status_payload(session, row, original)


@client_router.post("/sessions/{identity}/recover")
def recover(identity: UUID, data: Recover, request: Request, session: Db):
    if not data.document_closed:
        fail(422, "document_open", WORD_CLOSE_MESSAGE)
    row, actor, anchor, original, now = client_context(
        session, request, identity, allow_expired_lease=True, allow_finished=True
    )
    connection = session.get(OfficeAgentConnection, row.connection_id) if row.connection_id else None
    command = session.scalar(select(OfficeAgentCommand).where(
        OfficeAgentCommand.id == data.command_id,
    ).with_for_update().execution_options(populate_existing=True))
    if (
        connection is None or command is None or command.action != "recover"
        or command.session_id != row.id or command.connection_id != connection.id
        or connection.auth_session_id != row.auth_session_id or connection.user_id != row.user_id
    ):
        fail(404, "not_found", "Команда восстановления не найдена")
    if connection.expires_at <= now:
        fail(401, "agent_token_invalid", "Повторно подключите ACTI Sync")
    if not recovery_supported(connection):
        fail(409, "client_upgrade_required", "Для восстановления обновите ACTI Sync до 1.4.0")
    # A lost ACK can replay only this exact, durably committed preservation receipt.
    if command.state == "succeeded" and command.recovery_sha256 == data.local_sha256 and row.finished_at:
        return status_payload(session, row, original)
    if command.state != "pending" or command.expires_at <= now or row.finished_at:
        fail(409, "recovery_command_invalid", "Обновите состояние и повторите восстановление")
    command_access(session, actor, connection, command)
    command.recovery_sha256, command.state, command.error = data.local_sha256, "succeeded", None
    row.finished_at, row.state, row.last_seen_at, row.lease_expires_at = now, "finished", now, now
    if row.document_open is True:
        row.document_open, row.closed_at = False, now
    # A local preservation receipt never appends a revision or touches any other lease.
    emit(session, request, actor, "document.sync.recovered", "document_file", anchor.id, {
        "session_id": str(row.id), "command_id": str(command.id), "local_sha256": data.local_sha256,
    })
    return status_payload(session, row, original)


@router.get("/files/{file_id}/download")
def current_download(
    file_id: UUID,
    request: Request,
    session: Db,
    actor: Actor,
    mode: Literal["editor", "viewer"] | None = None,
):
    _, anchor, original = authorized(session, actor, file_id, mode=mode)
    return stream_original(
        request,
        session,
        current_file(session, anchor, original),
        original.revision if original else 1,
    )


@router.get("/files/{file_id}/status")
def file_status(
    file_id: UUID, request: Request, session: Db, actor: Actor,
    mode: Literal["editor", "viewer"] | None = None,
):
    _, anchor, original = authorized(session, actor, file_id, mode=mode)
    reason = "Открыт режим просмотра" if mode == "viewer" else None
    try:
        authorized(session, actor, file_id, write=True)
    except HTTPException as error:
        reason = (
            error.detail["message"]
            if isinstance(error.detail, dict)
            else "Редактирование недоступно"
        )
    now = datetime.now(UTC)
    row = session.scalar(
        select(OfficeEditSession)
        .where(OfficeEditSession.original_id == file_id)
        .order_by(
            func.coalesce(OfficeEditSession.last_seen_at, OfficeEditSession.created_at).desc(),
            OfficeEditSession.id,
        )
        .limit(1)
    )
    auth = browser_auth(session, request)
    payload = browser_file_payload(session, actor, anchor, original, row, reason, now, auth.id)
    connection = session.get(OfficeAgentConnection, row.connection_id) if row and row.connection_id else None
    payload["can_reopen"] = bool(
        reason is None and row and not row.finished_at
        and row.auth_session_id == auth.id and row.user_id == actor.id
        and row.lease_expires_at > now and row.expires_at > now
        and connection and connection.auth_session_id == auth.id
        and connection.user_id == actor.id and connection_online(connection, now)
    )
    return payload


def browser_file_payload(session, actor, anchor, original, row, reason, now, auth_id=None):
    file_id = anchor.id
    file = current_file(session, anchor, original)
    state = row.state if row else "idle"
    if row and row.finished_at is None and (row.expires_at <= now or row.lease_expires_at <= now):
        state = "offline" if row.exchanged_at else "error"
    if row and row.finished_at is None and row.lease_expires_at > now and row.expires_at > now:
        reason = reason or "Оригинал уже открыт в ACTI Sync"
    editor = session.get(User, row.user_id) if row else None
    connection = session.get(OfficeAgentConnection, row.connection_id) if row and row.connection_id else None
    telemetry_fresh = bool(
        row and row.user_id == actor.id and row.auth_session_id == auth_id and not row.finished_at
        and connection and connection.user_id == actor.id and connection.auth_session_id == auth_id
        and connection_online(connection, now) and row.expires_at > now
        and row.telemetry_seen_at and row.telemetry_seen_at >= now - timedelta(seconds=35)
        and row.last_seen_at and row.last_seen_at >= now - timedelta(seconds=35)
        and row.pending_uploads is not None
    )
    return {
        "file_id": file_id,
        "current_file_id": file.id,
        "filename": file.name,
        "revision": original.revision if original else 1,
        "sha256": file.sha256,
        "can_edit": reason is None,
        "reason": reason,
        "state": state,
        "session_id": row.id if row else None,
        "last_seen_at": row.last_seen_at if row else None,
        "last_saved_at": row.last_saved_at if row else None,
        "lease_expires_at": row.lease_expires_at if row else None,
        "document_open": row.document_open if row else None,
        "opened_at": row.opened_at if row else None,
        "closed_at": row.closed_at if row else None,
        "connection_id": row.connection_id if row else None,
        "telemetry_fresh": telemetry_fresh,
        "telemetry_seen_at": row.telemetry_seen_at if telemetry_fresh else None,
        "pending_uploads": row.pending_uploads if telemetry_fresh else None,
        "error_code": row.error_code if telemetry_fresh else None,
        "editor": {"id": editor.id, "display_name": editor.display_name} if editor else None,
    }


@router.get("/sessions/{identity}")
def browser_session(
    identity: UUID, request: Request, session: Db, actor: Actor,
    mode: Literal["editor", "viewer"] | None = None,
):
    row = session.get(OfficeEditSession, identity)
    if row is None:
        fail(404, "not_found", "Сеанс не найден")
    return file_status(row.original_id, request, session, actor, mode)


@router.get("/files/{file_id}/history")
def file_history(
    file_id: UUID, session: Db, actor: Actor, mode: Literal["editor", "viewer"] | None = None
):
    _, anchor, original = authorized(session, actor, file_id, mode=mode)
    rows = (
        session.execute(
            select(BinaryRevision.revision, DocumentFile, User.display_name)
            .join(DocumentFile, DocumentFile.id == BinaryRevision.file_id)
            .join(User, User.id == DocumentFile.created_by)
            .where(BinaryRevision.original_id == file_id)
            .order_by(BinaryRevision.revision.desc())
        ).all()
        if original
        else []
    )
    if not rows:
        rows = [(1, anchor, session.get(User, anchor.created_by).display_name)]
    return {
        "file_id": file_id,
        "items": [
            {
                "file_id": file.id,
                "filename": file.name,
                "revision": revision,
                "sha256": file.sha256,
                "size": file.size,
                "created_at": file.created_at,
                "created_by": file.created_by,
                "created_by_name": name,
            }
            for revision, file, name in rows
        ],
    }


class CommandInput(Input):
    session_id: UUID
    action: Literal["open", "retry", "finish", "reveal", "recover"]


class CommandResult(Input):
    id: UUID
    ok: bool
    error: str | None = Field(default=None, max_length=256)


class AgentHeartbeat(Input):
    command_results: list[CommandResult] = Field(default_factory=list, max_length=20)
    client_version: str | None = Field(
        default=None, max_length=32, pattern=r"^1\.[0-9]{1,5}\.[0-9]{1,5}$"
    )


def browser_auth(session, request):
    return session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == token_digest(request.cookies[COOKIE_NAME])
        )
    )


def require_interactive(actor):
    if (
        actor.must_change_password
        or actor.scoped_operations is not None
        or actor.scoped_registry_ids is not None
    ):
        fail(403, "interactive_session_required", "Нужна пользовательская сессия")


def require_https(request):
    origin = urlsplit(request.app.state.settings.origin)
    if origin.scheme != "https" or not origin.netloc or origin.username:
        fail(503, "office_https_required", "Для ACTI Sync требуется HTTPS")


def issue_agent_connection(session, owner, version, now):
    # Serialize first connection creation and token rotation per browser, including
    # concurrent exchanges for distinct documents. Existing document bindings survive.
    auth = session.scalar(
        select(AuthSession)
        .where(AuthSession.id == owner.auth_session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    verify_browser(session, owner, now)
    connection = session.scalar(
        select(OfficeAgentConnection)
        .where(OfficeAgentConnection.auth_session_id == auth.id)
        .with_for_update()
    )
    token = secrets.token_urlsafe(32)
    if connection is None:
        connection = OfficeAgentConnection(auth_session_id=auth.id, user_id=owner.user_id)
        session.add(connection)
    connection.token_hash = token_digest(token)
    connection.expires_at = min(auth.expires_at, now + timedelta(seconds=TOKEN_SECONDS))
    connection.client_version = version
    session.flush()
    return {"connection_id": connection.id, "token": token, "expires_at": connection.expires_at}


def agent_context(session, request, identity):
    row = session.scalar(
        select(OfficeAgentConnection)
        .where(OfficeAgentConnection.id == identity)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    value = request.headers.get("Authorization", "")
    token = value[7:] if value.startswith("Bearer ") else ""
    now = datetime.now(UTC)
    if (
        row is None
        or len(token) != 43
        or row.expires_at <= now
        or not secrets.compare_digest(row.token_hash, token_digest(token))
    ):
        fail(401, "agent_token_invalid", "Повторно подключите ACTI Sync")
    actor = verify_browser(session, row, now)
    require_interactive(actor)
    return row, actor, now


def connection_online(row, now):
    return bool(
        row
        and row.expires_at > now
        and row.last_seen_at
        and row.last_seen_at >= now - timedelta(seconds=35)
    )


def recovery_supported(connection):
    return bool(connection and re.fullmatch(r"1\.[0-9]{1,5}\.[0-9]{1,5}", connection.client_version)
        and tuple(int(part) for part in connection.client_version.split(".")) >= (1, 4, 0))


def expire_command(row, now):
    if row.state == "pending" and row.expires_at <= now:
        row.state, row.error = "expired", None


def command_access(session, actor, connection, command, *, allow_finished=False):
    row = session.get(OfficeEditSession, command.session_id)
    if (
        row is None
        or row.auth_session_id != connection.auth_session_id
        or row.user_id != connection.user_id
        or row.connection_id != connection.id
    ):
        fail(404, "not_found", "Сеанс не найден")
    if row.finished_at and not allow_finished:
        fail(409, "sync_finished", "Сеанс завершён")
    authorized(session, actor, row.original_id, write=True)
    return row


@router.post("/agent/connect")
def agent_connect(request: Request, session: Db, actor: Actor):
    require_interactive(actor)
    require_https(request)
    auth, token, now = browser_auth(session, request), secrets.token_urlsafe(32), datetime.now(UTC)
    session.scalar(select(AuthSession).where(AuthSession.id == auth.id).with_for_update())
    # A bounded pending-ticket set also prevents indefinite accumulation by retries.
    pending = list(
        session.scalars(
            select(OfficeAgentTicket).where(
                OfficeAgentTicket.auth_session_id == auth.id,
                OfficeAgentTicket.exchanged_at.is_(None),
                OfficeAgentTicket.expires_at > now,
            )
        )
    )
    if len(pending) >= 10:
        fail(429, "agent_connect_pending", "Дождитесь подключения ACTI Sync")
    ticket = OfficeAgentTicket(
        auth_session_id=auth.id,
        user_id=actor.id,
        ticket_hash=token_digest(token),
        expires_at=now + timedelta(seconds=60),
    )
    session.add(ticket)
    return {"uri": "acti-sync://connect?ticket=" + token, "expires_at": ticket.expires_at}


@client_router.post("/agent/exchange")
def agent_exchange(data: Exchange, request: Request, session: Db):
    ticket = session.scalar(
        select(OfficeAgentTicket)
        .where(OfficeAgentTicket.ticket_hash == token_digest(data.ticket))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = datetime.now(UTC)
    if ticket is None:
        fail(401, "agent_ticket_invalid", "Ссылка подключения недействительна")
    if ticket.exchanged_at or ticket.expires_at <= now:
        fail(409, "agent_ticket_consumed", "Ссылка подключения истекла или уже использована")
    actor = verify_browser(session, ticket, now)
    require_interactive(actor)
    result = issue_agent_connection(session, ticket, data.client_version, now)
    ticket.exchanged_at = now
    return result


@client_router.post("/agents/{identity}/renew")
def agent_renew(identity: UUID, request: Request, session: Db):
    connection, _, now = agent_context(session, request, identity)
    token = secrets.token_urlsafe(32)
    auth = session.get(AuthSession, connection.auth_session_id)
    connection.token_hash = token_digest(token)
    connection.expires_at = min(auth.expires_at, now + timedelta(seconds=TOKEN_SECONDS))
    return {"connection_id": connection.id, "token": token, "expires_at": connection.expires_at}


@client_router.post("/agents/{identity}/heartbeat")
def agent_heartbeat(identity: UUID, data: AgentHeartbeat, request: Request, session: Db):
    connection, actor, now = agent_context(session, request, identity)
    connection.last_seen_at = now
    if data.client_version is not None:
        connection.client_version = data.client_version
    pending = list(
        session.scalars(
            select(OfficeAgentCommand)
            .where(
                OfficeAgentCommand.connection_id == identity, OfficeAgentCommand.state == "pending"
            )
            .order_by(OfficeAgentCommand.created_at, OfficeAgentCommand.id)
            .with_for_update()
        )
    )
    results = {result.id: result for result in data.command_results}
    commands = []
    for command in pending:
        expire_command(command, now)
        if command.state != "pending":
            continue
        if command.action == "recover" and not recovery_supported(connection):
            command.state, command.error = "failed", "Для восстановления обновите ACTI Sync до 1.4.0"
            continue
        result = results.get(command.id)
        try:
            command_access(
                session,
                actor,
                connection,
                command,
                allow_finished=bool(result and result.ok and command.action in {"finish", "recover"}),
            )
        except HTTPException:
            command.state, command.error = "failed", "Доступ к сеансу больше недоступен"
            continue
        if result:
            # Only the recovery endpoint can confirm durable copy preservation.
            ok = result.ok and command.action != "recover"
            command.state = "succeeded" if ok else "failed"
            # The client error may contain paths, credentials or exception internals.
            # Never store or relay arbitrary native exception text to the browser.
            command.error = (
                None if ok else WORD_CLOSE_MESSAGE
                if command.action == "recover" and result.error == "file_busy"
                else "ACTI Sync не выполнил действие; локальная копия сохранена"
            )
        else:
            commands.append(
                {
                    "id": command.id,
                    "session_id": command.session_id,
                    "action": command.action,
                    "expires_at": command.expires_at,
                }
            )
    return {"commands": commands}


@router.post("/agent/commands")
def browser_command(data: CommandInput, request: Request, session: Db, actor: Actor):
    require_interactive(actor)
    auth, now = browser_auth(session, request), datetime.now(UTC)
    row = session.get(OfficeEditSession, data.session_id)
    if row is None or row.auth_session_id != auth.id or row.user_id != actor.id:
        fail(404, "not_found", "Сеанс не найден")
    connection = session.scalar(
        select(OfficeAgentConnection)
        .where(OfficeAgentConnection.id == row.connection_id)
        .with_for_update()
    )
    if not connection_online(connection, now):
        fail(409, "agent_offline", "Подключите ACTI Sync на этом компьютере")
    if data.action == "recover" and not recovery_supported(connection):
        fail(409, "client_upgrade_required", "Для восстановления обновите ACTI Sync до 1.4.0")
    probe = OfficeAgentCommand(
        connection_id=connection.id,
        session_id=row.id,
        action=data.action,
        expires_at=now + timedelta(seconds=60),
    )
    command_access(session, actor, connection, probe)
    pending = list(
        session.scalars(
            select(OfficeAgentCommand)
            .where(
                OfficeAgentCommand.connection_id == connection.id,
                OfficeAgentCommand.state == "pending",
            )
            .with_for_update()
        )
    )
    for command in pending:
        expire_command(command, now)
    if sum(command.state == "pending" for command in pending) >= 20:
        fail(429, "agent_commands_pending", "Дождитесь выполнения предыдущих действий")
    session.add(probe)
    session.flush()
    return {"id": probe.id, "state": "pending"}


@router.get("/agent/status")
def agent_status(request: Request, session: Db, actor: Actor):
    auth, now = browser_auth(session, request), datetime.now(UTC)
    connections = list(
        session.scalars(
            select(OfficeAgentConnection).where(
                OfficeAgentConnection.auth_session_id == auth.id,
                OfficeAgentConnection.user_id == actor.id,
            )
        )
    )
    items = [
        {
            "connection_id": row.id,
            "online": connection_online(row, now),
            "last_seen_at": row.last_seen_at,
            "client_version": row.client_version,
        }
        for row in connections
    ]
    recent = max(connections, key=lambda row: row.last_seen_at or row.created_at, default=None)
    # Keep unfinished local copies recoverable even after the same browser opens a
    # newer session. Only the latest completed session per original stays in history.
    latest = (
        select(OfficeEditSession.id)
        .where(
            OfficeEditSession.auth_session_id == auth.id,
            OfficeEditSession.user_id == actor.id,
            OfficeEditSession.connection_id.is_not(None),
        )
        .distinct(OfficeEditSession.original_id)
        .order_by(
            OfficeEditSession.original_id,
            OfficeEditSession.created_at.desc(),
            OfficeEditSession.id.desc(),
        )
    )
    rows = session.scalars(
        select(OfficeEditSession)
        .where(
            OfficeEditSession.auth_session_id == auth.id,
            OfficeEditSession.user_id == actor.id,
            OfficeEditSession.connection_id.is_not(None),
            or_(OfficeEditSession.id.in_(latest), OfficeEditSession.finished_at.is_(None)),
        )
        .order_by(OfficeEditSession.finished_at.is_not(None), OfficeEditSession.created_at.desc())
        .limit(100)
    )
    documents = []
    for row in rows:
        try:
            document, anchor, original = authorized(session, actor, row.original_id)
        except HTTPException:
            continue
        reason, can_control = None, not bool(row.finished_at)
        try:
            authorized(session, actor, row.original_id, write=True)
        except HTTPException as error:
            reason, can_control = error.detail.get("message", "Редактирование недоступно"), False
        payload = browser_file_payload(session, actor, anchor, original, row, reason, now, auth.id)
        command = session.scalar(
            select(OfficeAgentCommand)
            .where(OfficeAgentCommand.session_id == row.id)
            .order_by(OfficeAgentCommand.created_at.desc(), OfficeAgentCommand.id.desc())
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if command:
            expire_command(command, now)
        connection = session.get(OfficeAgentConnection, row.connection_id)
        payload.update(
            {
                "document_id": document.id,
                "document_title": document.title,
                "can_control": can_control,
                "can_recover": bool(can_control and row.expires_at > now and connection_online(connection, now) and recovery_supported(connection)),
                "can_launch_current": bool(reason is None and row.finished_at and command and command.action == "recover" and command.state == "succeeded" and not active_session(session, row.original_id, now)),
                "command": {
                    "id": command.id,
                    "action": command.action,
                    "state": command.state,
                    "error": command.error,
                    "error_code": "file_busy" if command.action == "recover" and command.error == WORD_CLOSE_MESSAGE else None,
                }
                if command
                else None,
            }
        )
        documents.append(payload)
    return {
        "online": any(item["online"] for item in items),
        "last_seen_at": recent.last_seen_at if recent else None,
        "client_version": recent.client_version if recent else None,
        "connections": items,
        "documents": documents,
    }
