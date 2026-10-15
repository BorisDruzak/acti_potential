import re
from tempfile import SpooledTemporaryFile
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import event

from app.api.dependencies import current_actor, db_session
from app.api.errors import fail
from app.registry.document_contracts import DocumentOutput
from app.registry.documents import (
    append_history,
    check_revision,
    document_output,
    file_visible,
    get_document,
    get_schema,
    raw_values,
    resolve_file_field,
    write_values,
)
from app.registry.events import emit
from app.registry.models import DocumentFile, DocumentVersion
from app.registry.policy import field_writable
from app.storage.local import LocalFileStorage

router = APIRouter()
UPLOAD_LIMIT = 25 * 1024 * 1024
Db = Annotated[object, Depends(db_session, scope="function")]
Actor = Annotated[object, Depends(current_actor)]


@router.post("/documents/{document_id}/files", response_model=DocumentOutput, status_code=201)
async def upload_file(
    document_id: UUID,
    request: Request,
    session: Db,
    actor: Actor,
    filename: str = Query(min_length=1, max_length=255),
    revision: int = Query(ge=1),
    field_key: str | None = Query(None, max_length=255),
):
    document = get_document(session, actor, document_id, "upload")
    check_revision(document, revision)
    schema = get_schema(session, document)
    values = raw_values(document, schema)
    if field_key is not None:
        field, parent, index = resolve_file_field(schema, field_key, values)
        if field is None or field["type"] != "file":
            fail(422, "invalid_file_field", "Поле загрузки не найдено")
        if parent:
            parent_field = next(
                f for s in schema["sections"] for f in s["fields"] if f["key"] == parent
            )
            if not field_writable(parent_field, actor):
                fail(403, "field_forbidden", "Изменение группы не разрешено")
        if not field_writable(field, actor):
            fail(403, "field_forbidden", "Загрузка в это поле не разрешена")
        probe = type("FileProbe", (), {"field_key": field_key})()
        if not file_visible(schema, actor, probe, values):
            fail(403, "field_forbidden", "Загрузка в это поле не разрешена")
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if not filename or any(ord(c) < 32 or ord(c) == 127 for c in filename):
        fail(422, "invalid_filename", "Неверное имя файла")
    mime = request.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
    if not re.fullmatch(r"[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+", mime):
        mime = "application/octet-stream"
    storage = LocalFileStorage(request.app.state.settings.storage_root)
    size = 0
    with SpooledTemporaryFile(max_size=1024 * 1024, dir=storage.root) as source:
        async for chunk in request.stream():
            size += len(chunk)
            if size > UPLOAD_LIMIT:
                fail(413, "file_too_large", "Размер файла превышает 25 МиБ")
            source.write(chunk)
        if not size:
            fail(422, "empty_file", "Выберите непустой файл")
        source.seek(0)
        document = get_document(session, actor, document_id, "upload", lock=True)
        check_revision(document, revision)
        stored = storage.put(uuid4().hex, source)

    # Remove only the newly written uncommitted object after a transaction rollback.
    # Already committed versions are never overwritten or deleted.
    def cleanup(_session):
        try:
            storage.delete_if_allowed(stored.storage_key, allowed=True)
        except OSError:
            pass

    event.listen(session, "after_rollback", cleanup, once=True)
    document.current_version += 1
    document.revision += 1
    version = DocumentVersion(
        document_id=document.id, version=document.current_version, created_by=actor.id
    )
    session.add(version)
    session.flush()
    file = DocumentFile(
        document_id=document.id,
        version_id=version.id,
        field_key=field_key,
        name=filename,
        size=stored.size,
        mime=mime,
        sha256=stored.sha256,
        storage_key=stored.storage_key,
        created_by=actor.id,
    )
    session.add(file)
    session.flush()
    if field_key:
        field, parent, index = resolve_file_field(schema, field_key, values)
        target = values[parent][index] if parent else values
        key = field["key"]
        target[key] = [*(target.get(key) or []), str(file.id)]
    write_values(document, schema, values)
    append_history(session, document, schema, actor)
    emit(
        session,
        request,
        actor,
        "document.version.created",
        "document",
        document.id,
        {"version": document.current_version, "revision": document.revision},
    )
    session.flush()
    return document_output(session, document, actor)


@router.get("/files/{file_id}/download", response_class=StreamingResponse)
def download_file(
    file_id: UUID,
    request: Request,
    session: Db,
    actor: Actor,
    mode: Literal["editor", "viewer"] | None = None,
):
    file = session.get(DocumentFile, file_id)
    if file is None:
        fail(404, "not_found", "Файл не найден")
    document = get_document(session, actor, file.document_id, "download")
    schema = get_schema(session, document)
    if not file_visible(schema, actor, file, raw_values(document, schema), mode):
        fail(404, "not_found", "Файл не найден")
    storage = LocalFileStorage(request.app.state.settings.storage_root)
    try:
        stored = storage.stat(file.storage_key)
    except (OSError, ValueError):
        fail(404, "file_missing", "Оригинал временно недоступен; карточка документа сохранена")
    if stored.sha256 != file.sha256 or stored.size != file.size:
        fail(409, "file_integrity", "Проверка целостности оригинала не пройдена")
    emit(session, request, actor, "document.file.downloaded", "document_file", file.id)

    def content():
        with storage.open_stream(file.storage_key) as stream:
            while chunk := stream.read(1024 * 1024):
                yield chunk

    return StreamingResponse(
        content(),
        media_type=file.mime,
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(file.name, safe=""),
            "Content-Length": str(file.size),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
