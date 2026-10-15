import json
from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import and_, case, delete, false, func, or_, select

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
from app.registry.policy import accessible_document_clause, field_readable, permissions_for
from app.registry.value_validation import validate_values

router = APIRouter()
Db = Annotated[object, Depends(db_session, scope="function")]
Actor = Annotated[object, Depends(current_actor)]


def fields_for(session, actor, mode=None):
    allowed_types = {
        UUID(tid)
        for registry in session.scalars(select(Registry))
        if "read" in permissions_for(session, actor, registry.id)
        for tid in registry.document_type_ids
    }
    allowed_schemas = select(Document.schema_version_id).where(
        accessible_document_clause(session, actor)
    )
    return [
        (row.id, f)
        for row in session.scalars(
            select(FormSchema).where(
                FormSchema.state == "published",
                or_(
                    FormSchema.document_type_id.in_(allowed_types),
                    FormSchema.id.in_(allowed_schemas),
                ),
            )
        )
        for s in row.schema_data.get("sections", [])
        for f in s.get("fields", [])
        if field_readable(f, actor, mode) and not f.get("visible_when")
    ]


def field_expression(field):
    system = field.get("system")
    return getattr(Document, system) if system else Document.custom_data[field["key"]].astext


def safe_system_filter(fields, system, predicate):
    return or_(
        false(),
        *[
            and_(Document.schema_version_id == schema_id, predicate(field_expression(field)))
            for schema_id, field in fields
            if field.get("system") == system and field.get("filterable", False)
        ],
    )


@router.get("/documents", response_model=DocumentPage)
def list_documents(
    session: Db,
    actor: Actor,
    registry_id: UUID | None = None,
    document_type_id: UUID | None = None,
    q: str = Query("", max_length=255),
    reg_number: str | None = Query(None, max_length=255),
    title: str | None = Query(None, max_length=255),
    reg_date: date | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    status: str | None = Query(None, max_length=100),
    filters: str | None = Query(None, max_length=8000),
    sort: Literal["updated_at", "created_at", "reg_date", "reg_number", "title"] = "updated_at",
    direction: Literal["asc", "desc"] = "desc",
    page: int = Query(1, ge=1, le=1000000),
    page_size: int = Query(25, ge=1, le=100),
    mode: Literal["editor", "viewer"] | None = None,
):
    query = select(Document).where(accessible_document_clause(session, actor))
    if registry_id:
        query = query.where(Document.registry_id == registry_id)
    if document_type_id:
        query = query.where(Document.document_type_id == document_type_id)
    fields = fields_for(session, actor, mode)
    for system, value in [("reg_number", reg_number), ("title", title), ("status", status)]:
        if value is not None:
            query = query.where(safe_system_filter(fields, system, lambda col: col.contains(value)))
    if q:
        query = query.where(
            or_(
                safe_system_filter(fields, "title", lambda col: col.ilike("%" + q + "%")),
                safe_system_filter(fields, "reg_number", lambda col: col.ilike("%" + q + "%")),
            )
        )
    for value, operator in ((reg_date, "eq"), (date_from, "ge"), (date_to, "le")):
        if value:
            query = query.where(
                safe_system_filter(
                    fields,
                    "reg_date",
                    lambda col: (
                        col == value
                        if operator == "eq"
                        else col >= value
                        if operator == "ge"
                        else col <= value
                    ),
                )
            )
    if filters:
        try:
            extra = json.loads(filters)
        except ValueError:
            fail(422, "invalid_filter", "Неверный фильтр")
        if not isinstance(extra, dict) or len(extra) > 20:
            fail(422, "invalid_filter", "Неверный фильтр")
        for key, value in extra.items():
            options = [(sid, f) for sid, f in fields if f["key"] == key and f.get("filterable")]
            if not options or isinstance(value, (dict, list)):
                fail(422, "invalid_filter", "Поле недоступно для фильтрации")
            query = query.where(
                or_(
                    *[
                        and_(
                            Document.schema_version_id == sid,
                            Document.custom_data[field["key"]].contains([value])
                            if field["type"] == "multi_reference"
                            else field_expression(field)
                            == (str(value).lower() if isinstance(value, bool) else str(value)),
                        )
                        for sid, field in options
                    ]
                )
            )
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    order_column = getattr(Document, sort)
    if sort in {"title", "reg_number", "reg_date"}:
        allowed_schemas = [sid for sid, field in fields if field.get("system") == sort]
        order_column = case(
            (Document.schema_version_id.in_(allowed_schemas), order_column), else_=None
        )
    order = (
        order_column.asc().nulls_last() if direction == "asc" else order_column.desc().nulls_last()
    )
    rows = session.scalars(
        query.order_by(order, Document.id).offset((page - 1) * page_size).limit(page_size)
    )
    return {
        "items": [document_summary(session, row, actor, mode=mode) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


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
