"""Configuration endpoints for the shared schema renderer and administration."""

from copy import deepcopy
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import current_actor, current_admin, db_session
from app.api.errors import fail
from app.registry.events import emit
from app.registry.models import (
    Document,
    DocumentType,
    FormSchema,
    ReferenceItem,
    ReferenceSet,
    Registry,
)
from app.registry.policy import accessible_document_clause, permissions_for, redact_schema
from app.registry.validation import field_map, validate_schema

router = APIRouter()
Code = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,99}$")]
Name = Annotated[str, Field(min_length=1, max_length=255)]
Mode = Literal["editor", "viewer"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TypeInput(Input):
    code: Code
    name: Name
    description: str = Field(default="", max_length=5000)


class TypePatch(Input):
    code: Code | None = None
    name: Name | None = None
    description: str | None = Field(default=None, max_length=5000)


class RegistryInput(TypeInput):
    document_type_ids: list[UUID] = Field(default_factory=list, max_length=100)


class RegistryPatch(TypePatch):
    document_type_ids: list[UUID] | None = Field(default=None, max_length=100)


class TypeOutput(BaseModel):
    id: UUID
    code: str
    name: str
    description: str


class RegistryOutput(TypeOutput):
    document_type_ids: list[UUID]
    permissions: list[str]


class SchemaInput(Input):
    schema_content: dict = Field(alias="schema")


class VersionInput(Input):
    schema_content: dict | None = Field(default=None, alias="schema")


class SchemaOutput(BaseModel):
    id: UUID
    document_type_id: UUID
    version: int
    state: str
    schema_content: dict = Field(alias="schema")


class ReferenceInput(Input):
    code: Code
    name: Name
    registry_id: UUID | None = None


class ReferencePatch(Input):
    code: Code | None = None
    name: Name | None = None


class ReferenceOutput(BaseModel):
    id: UUID
    code: str
    name: str
    registry_id: UUID | None


class ItemInput(Input):
    label: Name
    active: StrictBool = True
    metadata: dict = Field(default_factory=dict)


class ItemPatch(Input):
    label: Name | None = None
    active: StrictBool | None = None
    metadata: dict | None = None


class ItemOutput(BaseModel):
    id: UUID
    reference_set_id: UUID
    label: str
    active: bool
    metadata: dict
    source_database: str | None = None
    source_entity: str | None = None
    source_id: str | None = None


def _flush(session):
    try:
        session.flush()
    except IntegrityError:
        fail(409, "configuration_conflict", "Код или значение уже используются")


def _get(session, model, object_id, lock=False):
    row = (
        session.scalar(
            select(model)
            .where(model.id == object_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if lock
        else session.get(model, object_id)
    )
    if row is None:
        fail(404, "not_found", "Объект не найден")
    return row


def _is_admin(actor):
    return "admin" in actor.roles


def _registries(session, actor):
    rows = list(session.scalars(select(Registry).order_by(Registry.name, Registry.id)))
    return (
        rows
        if _is_admin(actor)
        else [r for r in rows if "read" in permissions_for(session, actor, r.id)]
    )


def _type_ids(session, actor):
    return {str(t) for registry in _registries(session, actor) for t in registry.document_type_ids}


def _require_type_read(session, actor, type_id):
    if not _is_admin(actor) and str(type_id) not in _type_ids(session, actor):
        fail(404, "not_found", "Объект не найден")


def _schema_output(row, actor, mode=None):
    schema = (
        row.schema_data
        if _is_admin(actor) and mode is None
        else redact_schema(row.schema_data, actor, mode)
    )
    return {
        "id": row.id,
        "document_type_id": row.document_type_id,
        "version": row.version,
        "state": row.state,
        "schema": schema,
    }


def _registry_output(session, actor, row):
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "description": row.description,
        "document_type_ids": row.document_type_ids,
        "permissions": sorted(permissions_for(session, actor, row.id)),
    }


def _type_output(row):
    return {"id": row.id, "code": row.code, "name": row.name, "description": row.description}


def _reference_output(row):
    return {"id": row.id, "code": row.code, "name": row.name, "registry_id": row.registry_id}


def _readable_reference_ids(session, actor, mode=None):
    if _is_admin(actor) and mode is None:
        return None
    allowed = set()
    all_sets = list(session.scalars(select(ReferenceSet)))
    contexts = set()
    for registry in _registries(session, actor):
        if not registry.document_type_ids or "read" not in permissions_for(
            session, actor, registry.id
        ):
            continue
        schemas = session.scalars(
            select(FormSchema).where(
                FormSchema.state == "published",
                FormSchema.document_type_id.in_([UUID(i) for i in registry.document_type_ids]),
            )
        )
        for schema in schemas:
            contexts.add((registry.id, schema.id))
    contexts.update(
        session.execute(
            select(Document.registry_id, Document.schema_version_id)
            .where(accessible_document_clause(session, actor))
            .distinct()
        ).all()
    )
    for registry_id, schema_id in contexts:
        schema = session.get(FormSchema, schema_id)
        if schema is None or schema.state != "published":
            continue
        for field in field_map(redact_schema(schema.schema_data, actor, mode)).values():
            candidates = [ref for ref in all_sets if ref.registry_id in {None, registry_id}]
            if field.get("reference_set_id"):
                allowed.update(
                    ref.id for ref in candidates if field["reference_set_id"] == str(ref.id)
                )
            elif field.get("reference_code"):
                matches = [ref for ref in candidates if ref.code == field["reference_code"]]
                scoped = [ref for ref in matches if ref.registry_id == registry_id]
                allowed.update(ref.id for ref in (scoped or matches))
    return allowed


def _require_reference_read(session, actor, reference_id, mode=None):
    allowed = _readable_reference_ids(session, actor, mode)
    if allowed is not None and reference_id not in allowed:
        fail(404, "not_found", "Объект не найден")


def _validate_type_assignment(session, type_ids, registry_id=None):
    result = sorted({str(i) for i in type_ids})
    if result:
        found = set(
            session.scalars(
                select(DocumentType.id).where(DocumentType.id.in_([UUID(i) for i in result]))
            )
        )
        if len(found) != len(result):
            fail(422, "unknown_document_type", "Неизвестный тип документа")
        if registry_id is not None:
            for schema in session.scalars(
                select(FormSchema).where(
                    FormSchema.document_type_id.in_([UUID(i) for i in result]),
                    FormSchema.state == "published",
                )
            ):
                try:
                    _check_bindings(session, schema.schema_data, [registry_id])
                except ValueError as error:
                    fail(422, "invalid_reference_scope", str(error))
    return result


def _check_bindings(session, schema, registry_ids):
    for field in field_map(schema).values():
        if field["type"] not in {"reference", "multi_reference", "department"}:
            continue
        explicit = field.get("reference_set_id")
        code = field.get("reference_code")
        if not explicit and not code:
            raise ValueError("A reference or department field needs a reference set")
        if explicit:
            ref = session.get(ReferenceSet, UUID(explicit))
            if ref is None or any(ref.registry_id not in {None, r} for r in registry_ids):
                raise ValueError("Reference set is outside the assigned registry")
        elif registry_ids:
            for registry_id in registry_ids:
                found = session.scalar(
                    select(ReferenceSet.id).where(
                        ReferenceSet.code == code,
                        (ReferenceSet.registry_id == registry_id)
                        | ReferenceSet.registry_id.is_(None),
                    )
                )
                if found is None:
                    raise ValueError("Reference code does not resolve in the assigned registry")
        elif session.scalar(select(ReferenceSet.id).where(ReferenceSet.code == code)) is None:
            raise ValueError("Reference code does not resolve")


def _validated(session, row, value, publishing=False, actor=None):
    try:
        result = validate_schema(value)
        published = session.scalars(
            select(FormSchema).where(
                FormSchema.document_type_id == row.document_type_id, FormSchema.state == "published"
            )
        )
        for previous in published:
            validate_schema(result, previous=previous.schema_data)
        registry_ids = list(
            session.scalars(
                select(Registry.id).where(
                    Registry.document_type_ids.contains([str(row.document_type_id)])
                )
            )
        )
        if publishing:
            _check_bindings(session, result, registry_ids)
        for field in field_map(result).values():
            if (
                publishing
                and field["type"] == "department"
                and not (field.get("reference_set_id") or field.get("reference_code"))
            ):
                raise ValueError("A department field needs a reference set")
            if (
                field.get("reference_set_id")
                and session.get(ReferenceSet, UUID(field["reference_set_id"])) is None
            ):
                raise ValueError("Reference set does not exist")
            if publishing and field.get("default") is not None:
                from app.registry.value_validation import validate_value

                contexts = registry_ids
                if not contexts and field.get("reference_set_id"):
                    contexts = [
                        session.get(ReferenceSet, UUID(field["reference_set_id"])).registry_id
                    ]
                if not contexts and field.get("reference_code"):
                    contexts = list(
                        session.scalars(
                            select(ReferenceSet.registry_id).where(
                                ReferenceSet.code == field["reference_code"]
                            )
                        )
                    )
                for registry_id in contexts or [None]:
                    validate_value(session, actor, field, field["default"], registry_id)
        return result
    except ValueError as error:
        fail(422, "invalid_schema", str(error))


def _emit(session, request, actor, action, object_type, row):
    _flush(session)
    emit(session, request, actor, action, object_type, row.id)


@router.get("/registries", response_model=list[RegistryOutput])
def list_registries(session=Depends(db_session, scope="function"), actor=Depends(current_actor)):
    return [_registry_output(session, actor, row) for row in _registries(session, actor)]


@router.post("/registries", response_model=RegistryOutput, status_code=201)
def create_registry(
    data: RegistryInput,
    request: Request,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_admin),
):
    row = Registry(
        id=uuid4(),
        code=data.code,
        name=data.name,
        description=data.description,
        document_type_ids=[],
    )
    row.document_type_ids = _validate_type_assignment(session, data.document_type_ids, row.id)
    session.add(row)
    _emit(session, request, actor, "registry.created", "registry", row)
    return _registry_output(session, actor, row)


@router.patch("/registries/{registry_id}", response_model=RegistryOutput)
def update_registry(
    registry_id: UUID,
    data: RegistryPatch,
    request: Request,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_admin),
):
    row = _get(session, Registry, registry_id, lock=True)
    changes = data.model_dump(exclude_unset=True)
    if any(v is None for v in changes.values()):
        fail(422, "invalid_configuration", "Значение не может быть пустым")
    if "document_type_ids" in changes:
        changes["document_type_ids"] = _validate_type_assignment(
            session, changes["document_type_ids"], row.id
        )
    for key, value in changes.items():
        setattr(row, key, value)
    _emit(session, request, actor, "registry.updated", "registry", row)
    return _registry_output(session, actor, row)


@router.get("/document-types", response_model=list[TypeOutput])
def list_types(session=Depends(db_session, scope="function"), actor=Depends(current_actor)):
    allowed = _type_ids(session, actor)
    return [
        _type_output(row)
        for row in session.scalars(select(DocumentType).order_by(DocumentType.name))
        if _is_admin(actor) or str(row.id) in allowed
    ]


@router.post("/document-types", response_model=TypeOutput, status_code=201)
def create_type(
    data: TypeInput,
    request: Request,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_admin),
):
    row = DocumentType(**data.model_dump())
    session.add(row)
    _flush(session)
    session.add(
        FormSchema(
            document_type_id=row.id,
            version=1,
            state="draft",
            schema_data={"sections": [{"key": "main", "title": "Основные сведения", "fields": []}]},
        )
    )
    _emit(session, request, actor, "document_type.created", "document_type", row)
    return _type_output(row)


@router.patch("/document-types/{type_id}", response_model=TypeOutput)
def update_type(
    type_id: UUID,
    data: TypePatch,
    request: Request,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_admin),
):
    row = _get(session, DocumentType, type_id, lock=True)
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is None:
            fail(422, "invalid_configuration", "Значение не может быть пустым")
        setattr(row, key, value)
    _emit(session, request, actor, "document_type.updated", "document_type", row)
    return _type_output(row)


@router.post("/document-types/{type_id}/copy", response_model=TypeOutput, status_code=201)
def copy_type(
    type_id: UUID,
    data: TypeInput,
    request: Request,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_admin),
):
    _get(session, DocumentType, type_id)
    previous = session.scalar(
        select(FormSchema)
        .where(FormSchema.document_type_id == type_id)
        .order_by(FormSchema.version.desc())
    )
    row = DocumentType(**data.model_dump())
    session.add(row)
    _flush(session)
    session.add(
        FormSchema(
            document_type_id=row.id,
            version=1,
            state="draft",
            schema_data=deepcopy(previous.schema_data),
        )
    )
    _emit(session, request, actor, "document_type.created", "document_type", row)
    return _type_output(row)


@router.get("/document-types/{type_id}/versions", response_model=list[SchemaOutput])
def list_versions(
    type_id: UUID,
    mode: Mode | None = None,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_actor),
):
    _require_type_read(session, actor, type_id)
    _get(session, DocumentType, type_id)
    return [
        _schema_output(row, actor, mode)
        for row in session.scalars(
            select(FormSchema)
            .where(FormSchema.document_type_id == type_id)
            .order_by(FormSchema.version.desc())
        )
        if _is_admin(actor) or row.state == "published"
    ]


@router.post("/document-types/{type_id}/versions", response_model=SchemaOutput, status_code=201)
def create_version(
    type_id: UUID,
    data: VersionInput,
    request: Request,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_admin),
):
    _get(session, DocumentType, type_id, lock=True)
    previous = session.scalar(
        select(FormSchema)
        .where(FormSchema.document_type_id == type_id)
        .order_by(FormSchema.version.desc())
    )
    row = FormSchema(
        document_type_id=type_id,
        version=(previous.version if previous else 0) + 1,
        state="draft",
        schema_data={},
    )
    value = (
        data.schema_content
        if data.schema_content is not None
        else (
            previous.schema_data
            if previous
            else {"sections": [{"key": "main", "title": "Основные сведения", "fields": []}]}
        )
    )
    row.schema_data = _validated(session, row, value)
    session.add(row)
    _emit(session, request, actor, "form.schema.created", "form_schema", row)
    return _schema_output(row, actor)


@router.get("/form-schemas/{schema_id}", response_model=SchemaOutput)
def get_schema(
    schema_id: UUID,
    mode: Mode | None = None,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_actor),
):
    row = _get(session, FormSchema, schema_id)
    _require_type_read(session, actor, row.document_type_id)
    if not _is_admin(actor) and row.state != "published":
        fail(404, "not_found", "Объект не найден")
    return _schema_output(row, actor, mode)


@router.put("/form-schemas/{schema_id}", response_model=SchemaOutput)
def update_schema(
    schema_id: UUID,
    data: SchemaInput,
    request: Request,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_admin),
):
    row = _get(session, FormSchema, schema_id, lock=True)
    if row.state != "draft":
        fail(409, "schema_immutable", "Опубликованная версия неизменяема; создайте новую версию")
    row.schema_data = _validated(session, row, data.schema_content)
    _emit(session, request, actor, "form.schema.updated", "form_schema", row)
    return _schema_output(row, actor)


@router.post("/form-schemas/{schema_id}/publish", response_model=SchemaOutput)
def publish_schema(
    schema_id: UUID,
    request: Request,
    data: Input,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_admin),
):
    before_lock = _get(session, FormSchema, schema_id)
    _get(session, DocumentType, before_lock.document_type_id, lock=True)
    row = _get(session, FormSchema, schema_id, lock=True)
    if row.state != "draft":
        fail(409, "schema_immutable", "Версия уже опубликована")
    row.schema_data = _validated(session, row, row.schema_data, publishing=True, actor=actor)
    row.state = "published"
    _emit(session, request, actor, "form.schema.published", "form_schema", row)
    return _schema_output(row, actor)


@router.get("/references", response_model=list[ReferenceOutput])
def list_references(
    mode: Mode | None = None,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_actor),
):
    allowed = _readable_reference_ids(session, actor, mode)
    return [
        _reference_output(row)
        for row in session.scalars(select(ReferenceSet).order_by(ReferenceSet.name))
        if allowed is None or row.id in allowed
    ]


@router.post("/references", response_model=ReferenceOutput, status_code=201)
def create_reference(
    data: ReferenceInput,
    request: Request,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_admin),
):
    if data.registry_id is not None:
        _get(session, Registry, data.registry_id)
    row = ReferenceSet(**data.model_dump())
    session.add(row)
    _emit(session, request, actor, "reference.created", "reference", row)
    return _reference_output(row)


@router.patch("/references/{reference_id}", response_model=ReferenceOutput)
def update_reference(
    reference_id: UUID,
    data: ReferencePatch,
    request: Request,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_admin),
):
    row = _get(session, ReferenceSet, reference_id, lock=True)
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is None:
            fail(422, "invalid_configuration", "Значение не может быть пустым")
        if key == "code" and value != row.code:
            # Published schemas can bind a set through its stable code.
            for form in session.scalars(select(FormSchema).where(FormSchema.state == "published")):
                if any(
                    f.get("reference_code") == row.code
                    for f in field_map(form.schema_data).values()
                ):
                    fail(409, "reference_code_in_use", "Код используется опубликованной формой")
        setattr(row, key, value)
    _emit(session, request, actor, "reference.updated", "reference", row)
    return _reference_output(row)


def _item_output(row, actor, mode=None):
    configuration = _is_admin(actor) and mode is None
    return {
        "id": row.id,
        "reference_set_id": row.reference_set_id,
        "label": row.label,
        "active": row.active,
        "metadata": row.item_metadata if configuration else {},
        "source_database": row.source_database if configuration else None,
        "source_entity": row.source_entity if configuration else None,
        "source_id": row.source_id if configuration else None,
    }


@router.get("/references/{reference_id}/items", response_model=list[ItemOutput])
def list_items(
    reference_id: UUID,
    mode: Mode | None = None,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_actor),
):
    _require_reference_read(session, actor, reference_id, mode)
    _get(session, ReferenceSet, reference_id)
    return [
        _item_output(row, actor, mode)
        for row in session.scalars(
            select(ReferenceItem)
            .where(ReferenceItem.reference_set_id == reference_id)
            .order_by(ReferenceItem.label, ReferenceItem.id)
        )
    ]


@router.post("/references/{reference_id}/items", response_model=ItemOutput, status_code=201)
def create_item(
    reference_id: UUID,
    data: ItemInput,
    request: Request,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_admin),
):
    _get(session, ReferenceSet, reference_id)
    row = ReferenceItem(
        reference_set_id=reference_id,
        label=data.label,
        active=data.active,
        item_metadata=data.metadata,
    )
    session.add(row)
    _emit(session, request, actor, "reference.item.created", "reference_item", row)
    return _item_output(row, actor)


@router.patch("/references/{reference_id}/items/{item_id}", response_model=ItemOutput)
def update_item(
    reference_id: UUID,
    item_id: UUID,
    data: ItemPatch,
    request: Request,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_admin),
):
    row = _get(session, ReferenceItem, item_id, lock=True)
    if row.reference_set_id != reference_id:
        fail(404, "not_found", "Объект не найден")
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is None:
            fail(422, "invalid_configuration", "Значение не может быть пустым")
        setattr(row, "item_metadata" if key == "metadata" else key, value)
    _emit(session, request, actor, "reference.item.updated", "reference_item", row)
    return _item_output(row, actor)
