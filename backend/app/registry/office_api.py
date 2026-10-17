"""Short-lived, session-bound, read-only desktop Office handoff for immutable files."""
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import COOKIE_NAME, current_actor, db_session
from app.api.errors import fail
from app.identity.sessions import resolve_actor_by_hash, token_digest
from app.registry.documents import file_visible, get_document, get_schema, raw_values
from app.registry.events import emit
from app.registry.models import DocumentFile
from app.storage.local import LocalFileStorage

router = APIRouter()
public = APIRouter()
Db = Annotated[object, Depends(db_session, scope="function")]
TTL = 120
APPLICATIONS = {
    "word": ("ms-word", frozenset({".doc", ".docx", ".rtf", ".odt"})),
    "libreoffice": ("vnd.libreoffice.command", frozenset({".doc", ".docx", ".rtf", ".odt", ".pdf", ".txt"})),
}
SAFE_MIME = {
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".rtf": "application/rtf",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8",
}


class OfficeOpenInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application: Literal["word", "libreoffice"]
    mode: Literal["editor", "viewer"] | None = None


class OfficeOpenOutput(BaseModel):
    uri: str
    expires_at: datetime


def authorized_original(session, actor, file_id, mode, request):
    file = session.get(DocumentFile, file_id)
    if file is None:
        fail(404, "not_found", "Файл не найден")
    document = get_document(session, actor, file.document_id, "download", mode=mode)
    schema = get_schema(session, document)
    if not file_visible(schema, actor, file, raw_values(document, schema), mode):
        fail(404, "not_found", "Файл не найден")
    storage = LocalFileStorage(request.app.state.settings.storage_root)
    try:
        stored = storage.stat(file.storage_key)
    except (OSError, ValueError):
        fail(404, "file_missing", "Оригинал временно недоступен")
    if (stored.sha256, stored.size) != (file.sha256, file.size):
        fail(409, "file_integrity", "Проверка целостности оригинала не пройдена")
    return file, storage


@router.post("/files/{file_id}/office-open", response_model=OfficeOpenOutput)
def office_open(
    file_id: UUID, data: OfficeOpenInput, request: Request, session: Db,
    actor: Annotated[object, Depends(current_actor)],
):
    file, _ = authorized_original(session, actor, file_id, data.mode, request)
    scheme, extensions = APPLICATIONS[data.application]
    if PurePath(file.name).suffix.lower() not in extensions:
        fail(422, "office_format", "Формат файла не поддерживается выбранным приложением")
    origin = request.app.state.settings.origin.rstrip("/")
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
        fail(503, "office_https_required", "Для открытия оригинала требуется защищённый адрес ACTI")
    token = secrets.token_urlsafe(32)
    key = "office:open:" + hashlib.sha256(token.encode()).hexdigest()
    payload = {"file_id": str(file_id), "session_hash": token_digest(request.cookies[COOKIE_NAME]), "mode": data.mode}
    request.app.state.redis.set(key, json.dumps(payload), ex=TTL)
    uri = f"{scheme}:ofv|u|{origin}/api/v1/office/files/{file_id}/{quote(file.name, safe='')}?ticket={token}"
    emit(session, request, actor, "document.file.opened", "document_file", file.id)
    return {"uri": uri, "expires_at": datetime.now(UTC) + timedelta(seconds=TTL)}


@public.api_route("/office/files/{file_id}/{filename}", methods=["GET", "HEAD"], include_in_schema=False)
def read_office_original(
    file_id: UUID, filename: str, request: Request, session: Db,
    ticket: str = Query(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$"),
):
    key = "office:open:" + hashlib.sha256(ticket.encode()).hexdigest()
    packed = request.app.state.redis.get(key)
    if packed is None:
        fail(404, "office_link_expired", "Ссылка открытия истекла. Откройте оригинал из карточки заново.")
    payload = json.loads(packed)
    if payload["file_id"] != str(file_id):
        fail(404, "not_found", "Файл не найден")
    actor = resolve_actor_by_hash(session, payload["session_hash"], datetime.now(UTC))
    if actor.must_change_password:
        fail(403, "password_change_required", "Сначала смените первоначальный пароль")
    file, storage = authorized_original(session, actor, file_id, payload["mode"], request)
    if filename != file.name:
        fail(404, "not_found", "Файл не найден")
    response_headers = {
        # Uploaded MIME is untrusted; this public same-origin capability must
        # never become an HTML or JavaScript hosting endpoint in a browser.
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(file.name, safe=""),
        "Content-Length": str(file.size), "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }
    mime = SAFE_MIME.get(PurePath(file.name).suffix.lower(), "application/octet-stream")
    if request.method == "HEAD":
        return Response(headers=response_headers, media_type=mime)

    def chunks():
        with storage.open_stream(file.storage_key) as stream:
            while block := stream.read(1024 * 1024):
                yield block

    return StreamingResponse(chunks(), media_type=mime, headers=response_headers)
