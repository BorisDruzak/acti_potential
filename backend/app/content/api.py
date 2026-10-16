from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.dependencies import current_actor, current_admin, db_session
from app.api.errors import fail
from app.content.contracts import SearchInput, SearchRead
from app.content.engine import search_errors
from app.content.indexing import queue_file
from app.content.models import ContentExtraction, DocumentBlock, DocumentIndexState
from app.content.search import (
    ContentExportParameters,
    ContentSearchParameters,
    matching_ids,
    search_response,
)
from app.registry.documents import file_visible, get_document, get_schema, raw_values
from app.registry.events import emit
from app.registry.models import DocumentFile, DocumentVersion
from app.service_api.contracts import ExtractionRead, Page

router = APIRouter()
Db = Annotated[object, Depends(db_session, scope="function")]
Actor = Annotated[object, Depends(current_actor)]
Mode = Literal["editor", "viewer"]


@router.get("/documents/{document_id}/search-index")
def document_search_index(document_id: UUID, session: Db, actor: Actor):
    document = get_document(session, actor, document_id)
    state = session.get(DocumentIndexState, document.id)
    return {
        "ready": bool(
            state and state.document_revision == document.revision and state.index_version == 2
        )
    }


@router.post("/search", response_model=SearchRead)
def unified_search(params: SearchInput, request: Request, session: Db, actor: Actor):
    from app.content.engine import execute_search

    settings = request.app.state.settings
    return execute_search(
        session,
        actor,
        params,
        secret=settings.session_secret.get_secret_value(),
        weights=settings.search_weights,
        synonyms=settings.search_synonyms,
        algorithm=settings.search_algorithm_version,
    )


@router.get("/search/export")
@search_errors
def unified_export(
    request: Request,
    session: Db,
    actor: Actor,
    parameters: str = Query(max_length=16000),
    format: Literal["csv", "xlsx"] = "csv",
    columns: str | None = None,
):
    from pydantic import ValidationError

    from app.content.engine import candidate_query, configure_search
    from app.registry.document_export import export_documents_response
    from app.registry.document_search import ExportParameters

    try:
        params = SearchInput.model_validate_json(parameters)
    except (ValidationError, ValueError):
        fail(422, "invalid_search", "Неверные условия поиска")
    settings = request.app.state.settings
    configure_search(session)
    query, sources, _ = candidate_query(
        session, actor, params, weights=settings.search_weights, synonyms=settings.search_synonyms
    )
    if sources and params.sort in {"auto", "relevance"}:
        from app.registry.models import Document

        query = query.order_by(None).order_by(sources[1].c.score.desc(), Document.id)
    export_params = ExportParameters(
        **params.legacy_filters().model_dump(), format=format, columns=columns
    )
    return export_documents_response(request, session, actor, export_params, document_query=query)


class ContentBlockRead(BaseModel):
    id: UUID
    kind: str
    ordinal: int
    parent_id: UUID | None
    page: int | None
    anchor: str
    text: str
    table: list[list[str]] | None


class ExtractionWithBlocks(ExtractionRead):
    blocks: list[ContentBlockRead]
    truncated: bool


def authorize_file(session, actor, file, mode=None, *, action="download"):
    if file is None:
        fail(404, "content_not_found", "Файл не найден")
    document = get_document(session, actor, file.document_id, action, mode=mode)
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


@router.get("/documents/{document_id}/content", response_model=Page[ExtractionWithBlocks])
def document_content(
    document_id: UUID,
    session: Db,
    actor: Actor,
    mode: Mode | None = None,
    file_id: UUID | None = None,
    page: int = Query(1, ge=1, le=1000000),
    page_size: int = Query(20, ge=1, le=100),
):
    document = get_document(session, actor, document_id, "download", mode=mode)
    schema, items = get_schema(session, document), []
    values, total = raw_values(document, schema), 0
    query = (
        select(ContentExtraction, DocumentFile, DocumentVersion.version)
        .join(DocumentFile, DocumentFile.id == ContentExtraction.file_id)
        .join(DocumentVersion, DocumentVersion.id == ContentExtraction.version_id)
        .where(ContentExtraction.document_id == document_id)
    )
    if file_id:
        file = session.get(DocumentFile, file_id)
        if file is None or file.document_id != document.id:
            fail(404, "content_not_found", "Файл не найден")
        authorize_file(session, actor, file, mode)
        query = query.where(ContentExtraction.file_id == file_id)
    for row, file, version in session.execute(
        query.order_by(
            DocumentVersion.version, ContentExtraction.created_at, ContentExtraction.id
        ).execution_options(yield_per=100)
    ):
        if file_visible(schema, actor, file, values, mode):
            if (page - 1) * page_size <= total < page * page_size:
                items.append(extraction_output(session, row, file, version))
            total += 1
    return {"items": items, "total": total, "page": page, "page_size": page_size}


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
@search_errors
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
