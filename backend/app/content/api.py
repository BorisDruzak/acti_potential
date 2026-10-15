from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from app.api.dependencies import current_actor, current_admin, db_session
from app.api.errors import fail
from app.content.indexing import queue_file
from app.content.models import ContentExtraction, DocumentBlock
from app.content.search import (
    ContentExportParameters,
    ContentSearchParameters,
    matching_ids,
    search_response,
)
from app.registry.documents import file_visible, get_document, get_schema, raw_values
from app.registry.events import emit
from app.registry.models import DocumentFile, DocumentVersion

router = APIRouter()
Db = Annotated[object, Depends(db_session, scope="function")]
Actor = Annotated[object, Depends(current_actor)]
Mode = Literal["editor", "viewer"]


def authorize_file(session, actor, file, mode=None, *, action="download"):
    if file is None:
        fail(404, "content_not_found", "Файл не найден")
    document = get_document(session, actor, file.document_id, action)
    schema = get_schema(session, document)
    if not file_visible(schema, actor, file, raw_values(document, schema), mode):
        fail(404, "content_not_found", "Файл не найден")
    return document


def block_output(row):
    return {
        **{
            key: getattr(row, key)
            for key in ("id", "kind", "ordinal", "parent_id", "page", "anchor", "text")
        },
        "table": row.table_data,
    }


def extraction_output(session, row, file, version, *, include_blocks=True):
    count = session.scalar(
        select(func.count()).select_from(DocumentBlock).where(DocumentBlock.extraction_id == row.id)
    )
    blocks = (
        list(
            session.scalars(
                select(DocumentBlock)
                .where(DocumentBlock.extraction_id == row.id)
                .order_by(DocumentBlock.ordinal)
                .limit(200)
            )
        )
        if include_blocks
        else []
    )
    return {
        **{
            key: getattr(row, key)
            for key in (
                "id",
                "file_id",
                "version_id",
                "sha256",
                "parser",
                "parser_version",
                "state",
                "attempts",
                "error_code",
                "warnings",
                "started_at",
                "completed_at",
            )
        },
        "document_id": row.document_id,
        "version": version,
        "name": file.name,
        "blocks": [block_output(block) for block in blocks],
        "block_count": count,
        "truncated": count > len(blocks),
    }


@router.get("/documents/{document_id}/content")
def document_content(document_id: UUID, session: Db, actor: Actor, mode: Mode | None = None):
    document = get_document(session, actor, document_id, "download")
    schema, items = get_schema(session, document), []
    values = raw_values(document, schema)
    for row, file, version in session.execute(
        select(ContentExtraction, DocumentFile, DocumentVersion.version)
        .join(DocumentFile, DocumentFile.id == ContentExtraction.file_id)
        .join(DocumentVersion, DocumentVersion.id == ContentExtraction.version_id)
        .where(ContentExtraction.document_id == document_id)
        .order_by(DocumentVersion.version, ContentExtraction.created_at)
    ):
        if file_visible(schema, actor, file, values, mode):
            items.append(extraction_output(session, row, file, version))
    return {"items": items}


@router.post("/files/{file_id}/extract", status_code=202)
def extract(file_id: UUID, request: Request, session: Db, actor: Actor, mode: Mode | None = None):
    file = session.get(DocumentFile, file_id)
    authorize_file(session, actor, file, mode, action="upload")
    row = queue_file(session, file, retry=True)
    if row.state == "failed" and row.attempts >= 3:
        fail(409, "content_retry_limit", "Достигнут предел трёх попыток обработки")
    emit(
        session,
        request,
        actor,
        "content.extraction.requested",
        "content_extraction",
        row.id,
        {"file_id": str(file.id)},
    )
    return {"id": row.id, "state": row.state}


@router.get("/content/extractions/{extraction_id}/blocks")
def blocks(
    extraction_id: UUID,
    session: Db,
    actor: Actor,
    mode: Mode | None = None,
    page: int = Query(1, ge=1, le=1000000),
    page_size: int = Query(200, ge=1, le=200),
):
    row = session.get(ContentExtraction, extraction_id)
    if row is None:
        fail(404, "content_not_found", "Обработка не найдена")
    authorize_file(session, actor, session.get(DocumentFile, row.file_id), mode)
    total = session.scalar(
        select(func.count()).select_from(DocumentBlock).where(DocumentBlock.extraction_id == row.id)
    )
    rows = session.scalars(
        select(DocumentBlock)
        .where(DocumentBlock.extraction_id == row.id)
        .order_by(DocumentBlock.ordinal)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [block_output(block) for block in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/content/search")
def search(session: Db, actor: Actor, params: Annotated[ContentSearchParameters, Query()]):
    return search_response(session, actor, params)


@router.get("/content/search/export")
def export(
    request: Request, session: Db, actor: Actor, params: Annotated[ContentExportParameters, Query()]
):
    from app.registry.document_export import export_documents_response

    identities = matching_ids(session, actor, params)
    return export_documents_response(request, session, actor, params, document_ids=identities)


@router.get("/admin/content/errors")
def errors(
    session: Db,
    actor=Depends(current_admin),
    mode: Mode | None = None,
    page: int = Query(1, ge=1, le=1000000),
    page_size: int = Query(20, ge=1, le=100),
):
    from fastapi import HTTPException

    items, total = [], 0
    for row, file, version in session.execute(
        select(ContentExtraction, DocumentFile, DocumentVersion.version)
        .join(DocumentFile, DocumentFile.id == ContentExtraction.file_id)
        .join(DocumentVersion, DocumentVersion.id == ContentExtraction.version_id)
        .where(ContentExtraction.state.in_(["failed", "encrypted", "unsupported", "no_text"]))
        .order_by(ContentExtraction.created_at.desc(), ContentExtraction.id)
        .execution_options(yield_per=100)
    ):
        try:
            authorize_file(session, actor, file, mode)
        except HTTPException as error:
            if error.status_code in {403, 404}:
                continue
            raise
        if (page - 1) * page_size <= total < page * page_size:
            items.append(extraction_output(session, row, file, version, include_blocks=False))
        total += 1
    return {"items": items, "total": total, "page": page, "page_size": page_size}
