from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import delete, select

from app.api.dependencies import current_actor, current_admin, db_session
from app.api.errors import fail
from app.identity.models import User
from app.registry.document_contracts import (
    ACLInput,
    DocumentCreate,
    DocumentOutput,
    DocumentPage,
    DocumentUpdate,
    HistoryOutput,
    UserOption,
)
from app.registry.document_search import (
    ExportParameters,
    SearchParameters,
    count_rows,
    search_query,
)
from app.registry.documents import (
    append_history,
    check_revision,
    document_output,
    document_summary,
    get_document,
    get_schema,
    raw_values,
    write_values,
)
from app.registry.events import emit
from app.registry.models import Document, DocumentACL, FormSchema, MetadataRevision, Registry
from app.registry.policy import permissions_for
from app.registry.value_validation import validate_values

router = APIRouter()
Db = Annotated[object, Depends(db_session, scope="function")]
Actor = Annotated[object, Depends(current_actor)]


@router.get("/documents", response_model=DocumentPage)
def list_documents(session: Db, actor: Actor, params: Annotated[SearchParameters, Query()]):
    query, _ = search_query(session, actor, params)
    total = count_rows(session, query)
    rows = session.scalars(query.offset((params.page - 1) * params.page_size).limit(params.page_size))
    return {"items": [document_summary(session, row, actor, mode=params.mode) for row in rows],
            "total": total, "page": params.page, "page_size": params.page_size}


@router.get("/documents/export")
def export_documents(request: Request, session: Db, actor: Actor,
                     params: Annotated[ExportParameters, Query()]):
    from app.registry.document_export import export_documents_response

    return export_documents_response(request, session, actor, params)


@router.post("/documents", response_model=DocumentOutput, status_code=201)
def create_document(data: DocumentCreate, request: Request, session: Db, actor: Actor):
    registry = session.get(Registry, data.registry_id)
    if registry is None or "create" not in permissions_for(session, actor, data.registry_id):
        fail(403, "forbidden", "Создание документов в реестре не разрешено")
    if "read" not in permissions_for(session, actor, data.registry_id):
        fail(403, "forbidden", "Нужен доступ к чтению реестра")
    if str(data.document_type_id) not in registry.document_type_ids:
        fail(422, "document_type_unavailable", "Тип не назначен реестру")
    query = select(FormSchema).where(
        FormSchema.document_type_id == data.document_type_id, FormSchema.state == "published"
    )
    if data.schema_version_id:
        query = query.where(FormSchema.id == data.schema_version_id)
    schema = session.scalar(query.order_by(FormSchema.version.desc()).limit(1))
    if schema is None:
        fail(422, "schema_unavailable", "Нет опубликованной формы")
    from app.registry.number_binding import validate_number_bindings

    try:
        validate_number_bindings(session, schema.schema_data, [registry.id], data.document_type_id)
    except ValueError as error:
        fail(422, "number_series_unavailable", str(error))
    document = Document(
        registry_id=registry.id,
        document_type_id=data.document_type_id,
        schema_version_id=schema.id,
        created_by=actor.id,
    )
    session.add(document)
    session.flush()
    proposed, trusted = data.values, set()
    if data.files:
        if "upload" not in permissions_for(session, actor, registry.id):
            fail(403, "forbidden", "Загрузка файлов в реестр не разрешена")
        from app.registry.attachments import create_attachments

        proposed, trusted = create_attachments(
            session, request, actor, document, schema.schema_data, data.values, data.files
        )
    values = validate_values(
        session,
        actor,
        schema.schema_data,
        proposed,
        registry.id,
        document_id=document.id,
        trusted_upload_ids=trusted,
    )
    write_values(document, schema.schema_data, values)
    append_history(session, document, schema.schema_data, actor)
    emit(
        session,
        request,
        actor,
        "document.created",
        "document",
        document.id,
        {
            "revision": document.revision,
            "registry_id": str(registry.id),
            "schema_version_id": str(schema.id),
        },
    )
    return document_output(session, document, actor)


@router.get("/documents/{document_id}", response_model=DocumentOutput)
def read_document(
    document_id: UUID, session: Db, actor: Actor, mode: Literal["editor", "viewer"] | None = None
):
    return document_output(session, get_document(session, actor, document_id), actor, mode)


@router.put("/documents/{document_id}", response_model=DocumentOutput)
def update_document(
    document_id: UUID, data: DocumentUpdate, request: Request, session: Db, actor: Actor
):
    document = get_document(session, actor, document_id, "update", lock=True)
    check_revision(document, data.revision)
    schema = get_schema(session, document)
    previous = raw_values(document, schema)
    document.revision += 1
    proposed, trusted = data.values, set()
    if data.files:
        get_document(session, actor, document_id, "upload")
        from app.registry.attachments import create_attachments
        from app.registry.policy import field_writable

        proposed, trusted = create_attachments(
            session, request, actor, document, schema, {**previous, **data.values}, data.files
        )
        changed = data.values.keys() | {
            f.field_key.split(".")[0] for f in data.files if f.field_key
        }
        proposed = {key: proposed[key] for key in changed}
        # Synthetic file-only group updates preserve inaccessible siblings by omission;
        # explicitly supplied keys still go through strict write authorization below.
        for section in schema["sections"]:
            for field in section["fields"]:
                key = field["key"]
                if field["type"] == "group" and key in proposed and key not in data.values:
                    allowed = {f["key"] for f in field["children"] if field_writable(f, actor)}
                    proposed[key] = [
                        {k: v for k, v in row.items() if k in allowed} for row in proposed[key]
                    ]
    values = validate_values(
        session,
        actor,
        schema,
        proposed,
        document.registry_id,
        previous,
        document_id=document.id,
        trusted_upload_ids=trusted,
    )
    write_values(document, schema, values)
    append_history(session, document, schema, actor)
    emit(
        session,
        request,
        actor,
        "document.metadata.updated",
        "document",
        document.id,
        {"revision": document.revision},
    )
    session.flush()
    return document_output(session, document, actor)


@router.get("/documents/{document_id}/history", response_model=list[HistoryOutput])
def history(
    document_id: UUID, session: Db, actor: Actor, mode: Literal["editor", "viewer"] | None = None
):
    from app.registry.policy import redact_values

    document = get_document(session, actor, document_id)
    schema = get_schema(session, document)
    rows = session.scalars(
        select(MetadataRevision)
        .where(MetadataRevision.document_id == document.id)
        .order_by(MetadataRevision.revision)
    )
    return [
        {
            "id": row.id,
            "revision": row.revision,
            "created_by": row.created_by,
            "created_at": row.created_at,
            "values": redact_values(schema, row.values, actor, mode),
        }
        for row in rows
    ]


@router.get("/documents/{document_id}/acl", response_model=ACLInput)
def get_acl(document_id: UUID, session: Db, actor=Depends(current_admin)):
    if session.get(Document, document_id) is None:
        fail(404, "not_found", "Документ не найден")
    entries = session.scalars(select(DocumentACL).where(DocumentACL.document_id == document_id))
    return {"entries": [{"user_id": r.user_id, "permissions": r.permissions} for r in entries]}


@router.put("/documents/{document_id}/acl", response_model=ACLInput)
def set_acl(
    document_id: UUID, data: ACLInput, request: Request, session: Db, actor=Depends(current_admin)
):
    if session.scalar(select(Document).where(Document.id == document_id).with_for_update()) is None:
        fail(404, "not_found", "Документ не найден")
    ids = [entry.user_id for entry in data.entries]
    if len(ids) != len(set(ids)) or any(session.get(User, uid) is None for uid in ids):
        fail(422, "invalid_acl", "Неверный список пользователей")
    session.execute(delete(DocumentACL).where(DocumentACL.document_id == document_id))
    session.add_all(
        DocumentACL(
            document_id=document_id,
            user_id=entry.user_id,
            permissions=sorted(set(entry.permissions)),
        )
        for entry in data.entries
    )
    emit(session, request, actor, "document.access.changed", "document", document_id)
    return data


@router.get("/users/options", response_model=list[UserOption])
def user_options(session: Db, actor: Actor, q: str = Query("", max_length=100)):
    # Authenticated local directory selector; credentials and account state are excluded.
    rows = session.scalars(
        select(User)
        .where(User.active.is_(True), User.display_name.ilike("%" + q + "%"))
        .order_by(User.display_name)
        .limit(100)
    )
    return [{"id": row.id, "display_name": row.display_name} for row in rows]
