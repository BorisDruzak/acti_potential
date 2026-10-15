from time import perf_counter
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.api.dependencies import db_session
from app.api.errors import fail
from app.content import api as content
from app.content.models import ContentExtraction, DocumentBlock
from app.execution import api as execution_api
from app.execution import service as execution
from app.execution.contracts import ListInput
from app.knowledge import service as knowledge
from app.knowledge.models import PracticeCase
from app.numbering import service as numbering
from app.numbering.models import NumberEntry
from app.processes import service as processes
from app.registry.document_search import SearchParameters, search_query
from app.registry.documents import (
    document_output,
    file_visible,
    get_document,
    get_schema,
    raw_values,
)
from app.registry.file_api import download_file
from app.registry.models import (
    DocumentFile,
    DocumentVersion,
    FormSchema,
    MetadataRevision,
    Registry,
)
from app.registry.policy import field_accessible, permissions_for, redact_schema, redact_values
from app.registry.validation import iter_fields
from app.relations import service as relations
from app.relations.models import DocumentRelation
from app.service_api.admin import usage
from app.service_api.auth import ServiceContext, current_service, operation
from app.service_api.contracts import (
    BlockRead,
    DocumentRead,
    ExecutionRead,
    ExtractionRead,
    FileEdition,
    FileRead,
    FormRead,
    KnowledgeRead,
    MetadataRead,
    NumberRead,
    Page,
    ProcessVersionRead,
    RelationRead,
    SeriesRead,
    StepRead,
)

router = APIRouter()
Db = Annotated[object, Depends(db_session, scope="function")]
Context = Annotated[ServiceContext, Depends(current_service)]


@router.get("/form-schemas/{schema_id}", response_model=FormRead)
def form_schema(schema_id: UUID, session: Db, context: Context):
    operation(context, "documents")
    form = session.get(FormSchema, schema_id)
    if (
        form is None
        or form.state != "published"
        or not any(
            str(form.document_type_id) in registry.document_type_ids
            and "read" in permissions_for(session, context.actor, registry.id)
            for registry in session.scalars(
                select(Registry).where(
                    Registry.id.in_([UUID(value) for value in context.actor.scoped_registry_ids])
                )
            )
        )
    ):
        absent()
    return {
        "id": form.id,
        "document_type_id": form.document_type_id,
        "version": form.version,
        "state": form.state,
        "schema": redact_schema(form.schema_data, context.actor, "viewer"),
    }


def absent():
    fail(404, "service_resource_not_found", "Ресурс не найден")


def scoped(context, registry_id):
    if str(registry_id) not in context.actor.scoped_registry_ids:
        absent()


def page_output(items, page, page_size):
    selected, total = [], 0
    offset = (page - 1) * page_size
    for item in items:
        if offset <= total < offset + page_size:
            selected.append(item)
        total += 1
    return {
        "items": jsonable_encoder(selected),
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def file_data(file):
    return {
        key: getattr(file, key)
        for key in ("id", "document_id", "version_id", "name", "mime", "size", "sha256")
    }


def allowed_file(session, context, identity):
    file = session.get(DocumentFile, identity)
    try:
        content.authorize_file(session, context.actor, file, "viewer")
    except HTTPException as error:
        if error.status_code in {403, 404}:
            absent()
        raise
    return file


def download_document(session, context, identity):
    try:
        return get_document(session, context.actor, identity, "download")
    except HTTPException as error:
        if error.status_code in {403, 404}:
            absent()
        raise


def document_data(session, context, document):
    data = document_output(session, document, context.actor, "viewer")
    data["files"] = []
    if "files" in context.actor.scoped_operations:
        schema = get_schema(session, document)
        for file in session.scalars(
            select(DocumentFile)
            .where(DocumentFile.document_id == document.id)
            .order_by(DocumentFile.created_at, DocumentFile.id)
        ):
            if file_visible(schema, context.actor, file, raw_values(document, schema), "viewer"):
                try:
                    allowed_file(session, context, file.id)
                except Exception as error:
                    from fastapi import HTTPException

                    if isinstance(error, HTTPException):
                        continue
                    raise
                data["files"].append(file_data(file))
    if not context.actor.scoped_operations.intersection({"files", "content"}):

        def omit_files(fields, values):
            for field in fields:
                if field["type"] == "file":
                    values.pop(field["key"], None)
                if field["type"] == "group":
                    for row in values.get(field["key"], []):
                        omit_files(field.get("children", []), row)

        omit_files(list(iter_fields(get_schema(session, document))), data["values"])
    return jsonable_encoder(data)


@router.get("/documents", response_model=Page[DocumentRead])
def documents(
    session: Db,
    context: Context,
    registry_id: UUID | None = None,
    document_type_id: UUID | None = None,
    q: str = Query("", max_length=255),
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
):
    operation(context, "documents")
    started = perf_counter()
    params = SearchParameters(
        registry_id=registry_id,
        document_type_id=document_type_id,
        q=q,
        mode="viewer",
        page=page,
        page_size=page_size,
    )
    query, _ = search_query(session, context.actor, params)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    items = [
        document_data(session, context, row)
        for row in session.scalars(
            query.order_by(None)
            .order_by(query.column_descriptions[0]["entity"].id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ]
    for identity in {item["registry_id"] for item in items}:
        usage(session, UUID(identity), "service.documents", int((perf_counter() - started) * 1000))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/documents/{document_id}", response_model=DocumentRead)
def document(document_id: UUID, session: Db, context: Context):
    operation(context, "documents")
    row = get_document(session, context.actor, document_id)
    usage(session, row.registry_id, "service.document_open", 0)
    return document_data(session, context, row)


def metadata_data(session, context, row):
    document = get_document(session, context.actor, row.document_id)
    schema = get_schema(session, document)
    fields = {field["key"]: field for field in iter_fields(schema)}
    current = raw_values(document, schema)
    values = redact_values(schema, row.values, context.actor, "viewer")
    values = {
        key: value
        for key, value in values.items()
        if field_accessible(fields[key], context.actor, fields, "viewer", values=current)
    }
    if not context.actor.scoped_operations.intersection({"files", "content"}):
        values = {key: value for key, value in values.items() if fields[key]["type"] != "file"}
    return {
        "id": row.id,
        "document_id": document.id,
        "revision": row.revision,
        "schema_version_id": document.schema_version_id,
        "values": values,
        "created_at": row.created_at,
    }


@router.get("/documents/{document_id}/metadata-revisions", response_model=Page[MetadataRead])
def metadata_history(
    document_id: UUID,
    session: Db,
    context: Context,
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
):
    operation(context, "documents")
    get_document(session, context.actor, document_id)
    query = select(MetadataRevision).where(MetadataRevision.document_id == document_id)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    return {
        "items": [
            metadata_data(session, context, row)
            for row in session.scalars(
                query.order_by(MetadataRevision.revision.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/metadata-revisions/{revision_id}", response_model=MetadataRead)
def metadata_revision(revision_id: UUID, session: Db, context: Context):
    operation(context, "documents")
    row = session.get(MetadataRevision, revision_id)
    if row is None:
        absent()
    return metadata_data(session, context, row)


def edition_data(session, context, edition):
    download_document(session, context, edition.document_id)
    files = []
    for file in session.scalars(
        select(DocumentFile).where(DocumentFile.version_id == edition.id).order_by(DocumentFile.id)
    ):
        try:
            allowed_file(session, context, file.id)
        except Exception as error:
            from fastapi import HTTPException

            if isinstance(error, HTTPException):
                continue
            raise
        files.append(file_data(file))
    if not files:
        absent()
    return {
        "id": edition.id,
        "document_id": edition.document_id,
        "version": edition.version,
        "created_at": edition.created_at,
        "files": files,
    }


@router.get("/documents/{document_id}/file-versions", response_model=Page[FileEdition])
def file_versions(
    document_id: UUID,
    session: Db,
    context: Context,
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
):
    operation(context, "files")
    download_document(session, context, document_id)
    items = []
    for edition in session.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version.desc())
    ):
        try:
            items.append(edition_data(session, context, edition))
        except Exception as error:
            from fastapi import HTTPException

            if not isinstance(error, HTTPException):
                raise
    return page_output(items, page, page_size)


@router.get("/file-versions/{version_id}", response_model=FileEdition)
def file_version(version_id: UUID, session: Db, context: Context):
    operation(context, "files")
    edition = session.get(DocumentVersion, version_id)
    if edition is None:
        absent()
    return edition_data(session, context, edition)


@router.get("/files/{file_id}", response_model=FileRead)
def file(file_id: UUID, session: Db, context: Context):
    operation(context, "files")
    return file_data(allowed_file(session, context, file_id))


@router.get(
    "/files/{file_id}/download",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Original file bytes; Content-Disposition contains an authorized filename",
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
def download(file_id: UUID, request: Request, session: Db, context: Context):
    operation(context, "files")
    file = allowed_file(session, context, file_id)
    return download_file(file.id, request, session, context.actor, "viewer")


@router.get("/documents/{document_id}/content", response_model=Page[ExtractionRead])
def extractions(
    document_id: UUID,
    session: Db,
    context: Context,
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
):
    operation(context, "content")
    download_document(session, context, document_id)
    items = []
    for extraction, file, version in session.execute(
        select(ContentExtraction, DocumentFile, DocumentVersion.version)
        .join(DocumentFile, DocumentFile.id == ContentExtraction.file_id)
        .join(DocumentVersion, DocumentVersion.id == ContentExtraction.version_id)
        .where(ContentExtraction.document_id == document_id)
        .order_by(DocumentVersion.version, ContentExtraction.created_at)
    ):
        try:
            allowed_file(session, context, file.id)
        except Exception as error:
            from fastapi import HTTPException

            if isinstance(error, HTTPException):
                continue
            raise
        items.append(
            content.extraction_output(session, extraction, file, version, include_blocks=False)
        )
    return page_output(items, page, page_size)


def block_data(session, context, block):
    extraction = session.get(ContentExtraction, block.extraction_id)
    allowed_file(session, context, extraction.file_id)
    return {
        **content.block_output(block),
        "document_id": block.document_id,
        "extraction_id": extraction.id,
        "file_id": extraction.file_id,
        "version_id": extraction.version_id,
    }


@router.get("/extractions/{extraction_id}/blocks", response_model=Page[BlockRead])
def blocks(
    extraction_id: UUID,
    session: Db,
    context: Context,
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(100, ge=1, le=200),
):
    operation(context, "content")
    extraction = session.get(ContentExtraction, extraction_id)
    if extraction is None:
        absent()
    allowed_file(session, context, extraction.file_id)
    query = select(DocumentBlock).where(DocumentBlock.extraction_id == extraction_id)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    return {
        "items": [
            block_data(session, context, row)
            for row in session.scalars(
                query.order_by(DocumentBlock.ordinal)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/blocks/{block_id}", response_model=BlockRead)
def block(block_id: UUID, session: Db, context: Context):
    operation(context, "content")
    row = session.get(DocumentBlock, block_id)
    if row is None:
        absent()
    return block_data(session, context, row)


def relation_data(session, context, row):
    resolved = relations.endpoints(session, context.actor, row, "viewer")
    return jsonable_encoder(
        relations.output(session, context.actor, row, "viewer", resolved=resolved)
    )


@router.get("/relations/{relation_id}", response_model=RelationRead)
def relation(relation_id: UUID, session: Db, context: Context):
    operation(context, "relations")
    row = session.get(DocumentRelation, relation_id)
    if row is None:
        absent()
    return relation_data(session, context, row)


@router.get("/documents/{document_id}/relations", response_model=Page[RelationRead])
def document_relations(
    document_id: UUID,
    session: Db,
    context: Context,
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
):
    operation(context, "relations")
    get_document(session, context.actor, document_id)
    from sqlalchemy import or_

    items = []
    for row in session.scalars(
        select(DocumentRelation)
        .where(
            or_(
                DocumentRelation.source_document_id == document_id,
                DocumentRelation.target_document_id == document_id,
            )
        )
        .order_by(DocumentRelation.created_at, DocumentRelation.id)
    ):
        try:
            items.append(relation_data(session, context, row))
        except Exception as error:
            from fastapi import HTTPException

            if not isinstance(error, HTTPException):
                raise
    return page_output(items, page, page_size)


@router.get("/process-versions/{version_id}", response_model=ProcessVersionRead)
def process_version(version_id: UUID, session: Db, context: Context):
    operation(context, "processes")
    process, version = processes.get_version(session, context.actor, version_id)
    usage(session, process.registry_id, "service.process_open", 0)
    return jsonable_encoder(
        processes.version_output(session, context.actor, process, version, "viewer")
    )


@router.get("/process-versions/{version_id}/steps/{step_key}", response_model=StepRead)
def process_step(version_id: UUID, step_key: str, session: Db, context: Context):
    data = process_version(version_id, session, context)
    step = next((row for row in data["steps"] if row["key"] == step_key), None)
    if step is None:
        absent()
    return step


@router.get("/template-versions/{version_id}", response_model=KnowledgeRead)
def template_version(version_id: UUID, session: Db, context: Context):
    operation(context, "knowledge")
    obj, version = knowledge.get_version(session, context.actor, version_id)
    return jsonable_encoder(
        knowledge.version_output(session, context.actor, obj, version, "viewer")
    )


@router.get("/cases/{case_id}", response_model=KnowledgeRead)
def practice(case_id: UUID, session: Db, context: Context):
    operation(context, "knowledge")
    obj = knowledge.get_object(session, context.actor, PracticeCase, case_id)
    return jsonable_encoder(knowledge.case_output(session, context.actor, obj, "viewer"))


def number_data(session, context, row):
    series = numbering.get_series(session, row.series_id)
    numbering.require(session, context.actor, series, "view")
    if not numbering.entry_visible(session, context.actor, row):
        absent()
    output = numbering.output(session, context.actor, row)
    if "documents" not in context.actor.scoped_operations:
        output["document_title"] = None
    return output


@router.get("/number-series/{series_id}", response_model=SeriesRead)
def number_series(series_id: UUID, session: Db, context: Context):
    operation(context, "numbers")
    row = numbering.get_series(session, series_id)
    numbering.require(session, context.actor, row, "view")
    return {
        key: getattr(row, key)
        for key in ("id", "registry_id", "code", "name", "state", "document_type_ids", "synthetic")
    }


@router.get("/number-entries/{entry_id}", response_model=NumberRead)
def number_entry(entry_id: UUID, session: Db, context: Context):
    operation(context, "numbers")
    entry = session.get(NumberEntry, entry_id)
    if entry is None:
        absent()
    return number_data(session, context, entry)


@router.get("/number-series/{series_id}/entries", response_model=Page[NumberRead])
def number_entries(
    series_id: UUID,
    session: Db,
    context: Context,
    period_key: str | None = None,
    state: str | None = None,
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
):
    operation(context, "numbers")
    series = numbering.get_series(session, series_id)
    items = (
        number_data(session, context, row)
        for row in numbering.visible_entries(
            session, context.actor, series, period=period_key, state=state
        )
    )
    return page_output(items, page, page_size)


def execution_data(session, context, instance, *, summary=False):
    output = execution.instance_output(session, context.actor, instance, "viewer", summary=summary)
    if "documents" not in context.actor.scoped_operations:
        from app.execution.models import StepExecution, StepResult

        for attempt, result in session.execute(
            select(StepExecution, StepResult)
            .join(StepResult, StepResult.id == StepExecution.result_id)
            .where(StepExecution.instance_id == instance.id, StepExecution.state == "completed")
        ):
            if result.result_document and any(
                edge.get("condition") and edge["from_step"] == attempt.step_key
                for edge in instance.snapshot["transitions"]
            ):
                output.update(
                    trajectory_restricted=True,
                    current_step_key=None,
                    current_step_execution_id=None,
                    steps=[],
                    history=[],
                    overdue=execution.overdue(instance, restricted=True),
                )
                break
        for attempt in output["steps"]:
            result = attempt.get("result")
            if result and result.get("result_document"):
                result["values"] = {}
                result["result_document"] = {
                    "restricted": True,
                    "label": "Документ результата ограничен",
                }
    return jsonable_encoder(output)


@router.get("/execution/instances/{instance_id}", response_model=ExecutionRead)
def instance(instance_id: UUID, session: Db, context: Context):
    operation(context, "execution")
    return execution_data(
        session, context, execution.get_instance(session, context.actor, instance_id)
    )


@router.get("/execution/instances", response_model=Page[ExecutionRead])
def instances(
    session: Db,
    context: Context,
    registry_id: UUID | None = None,
    state: str | None = None,
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
):
    operation(context, "execution")
    output = execution_api.listing(
        session,
        context.actor,
        ListInput(
            registry_id=registry_id, state=state, page=page, page_size=page_size, mode="viewer"
        ),
    )
    output["items"] = [
        execution_data(
            session,
            context,
            execution.get_instance(session, context.actor, UUID(str(item["id"]))),
            summary=True,
        )
        for item in output["items"]
    ]
    return output
