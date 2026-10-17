"""Configuration endpoints for the shared schema renderer and administration."""

import base64
import binascii
from copy import deepcopy
from datetime import date
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
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
from app.registry.presentation import configuration_schema, configuration_text
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
    description: str = Field(default="", max_length=5000)
    registry_id: UUID | None = None


class ReferencePatch(Input):
    code: Code | None = None
    name: Name | None = None
    description: str | None = Field(default=None, max_length=5000)


class ReferenceOutput(BaseModel):
    id: UUID
    code: str
    name: str
    description: str
    registry_id: UUID | None


class ItemInput(Input):
    label: Name
    active: StrictBool = True
    metadata: dict = Field(default_factory=dict)
    code: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=5000)
    sort_order: int = Field(default=0, ge=-1000000, le=1000000)
    parent_id: UUID | None = None
    valid_from: date | None = None
    valid_until: date | None = None


class ItemPatch(Input):
    label: Name | None = None
    active: StrictBool | None = None
    metadata: dict | None = None
    code: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    sort_order: int | None = Field(default=None, ge=-1000000, le=1000000)
    parent_id: UUID | None = None
    valid_from: date | None = None
    valid_until: date | None = None


class ItemOutput(BaseModel):
    id: UUID
    reference_set_id: UUID
    label: str
    active: bool
    code: str
    description: str
    sort_order: int
    parent_id: UUID | None
    valid_from: date | None
    valid_until: date | None
    selectable: bool
    metadata: dict
    source_database: str | None = None
    source_entity: str | None = None
    source_id: str | None = None


class PersonInput(Input):
    schema_version_id: UUID
    field_key: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")]
    label: Name
    document_id: UUID | None = None

    @field_validator("label", mode="before")
    @classmethod
    def normalize_label(cls, value):
        from app.registry.people_creation import normalized_label

        return normalized_label(value) if isinstance(value, str) else value


class PersonOutput(BaseModel):
    item: ItemOutput
    created: bool


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
        "schema": configuration_schema(schema),
    }


def _registry_output(session, actor, row):
    return {
        "id": row.id,
        "code": row.code,
        "name": configuration_text(row.name),
        "description": configuration_text(row.description),
        "document_type_ids": row.document_type_ids,
        "permissions": sorted(permissions_for(session, actor, row.id)),
    }


def _type_output(row):
    return {"id": row.id, "code": row.code, "name": configuration_text(row.name), "description": configuration_text(row.description)}


def _reference_output(row):
    return {
        "id": row.id,
        "code": row.code,
        "name": configuration_text(row.name),
        "registry_id": row.registry_id,
        "description": configuration_text(row.description),
    }


def _readable_reference_ids(session, actor, mode=None):
    # Preview modes change presentation, while administrators retain configuration
    # access before a draft is published or assigned to a registry.
    if _is_admin(actor):
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
            .where(accessible_document_clause(session, actor, mode))
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
                    _check_bindings(session, schema.schema_data, [registry_id], schema.document_type_id)
                except ValueError as error:
                    fail(422, "invalid_reference_scope", str(error))
    return result


def _check_bindings(session, schema, registry_ids, document_type_id=None):
    from app.registry.number_binding import validate_number_bindings

    validate_number_bindings(session, schema, registry_ids, document_type_id)
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
            _check_bindings(session, result, registry_ids, row.document_type_id)
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
    from app.registry.references import selectable

    configuration = _is_admin(actor) and mode is None
    return {
        "id": row.id,
        "reference_set_id": row.reference_set_id,
        "label": row.label,
        "active": row.active,
        **{
            key: getattr(row, key)
            for key in (
                "code",
                "description",
                "sort_order",
                "parent_id",
                "valid_from",
                "valid_until",
            )
        },
        "selectable": selectable(row),
        "metadata": row.item_metadata if configuration else {},
        "source_database": row.source_database if configuration else None,
        "source_entity": row.source_entity if configuration else None,
        "source_id": row.source_id if configuration else None,
    }


@router.post("/registries/{registry_id}/people", response_model=PersonOutput, status_code=201)
def create_registry_person(
    registry_id: UUID,
    data: PersonInput,
    request: Request,
    response: Response,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_actor),
):
    from app.registry.people_creation import create_person

    item, created = create_person(session, request, actor, registry_id, data)
    response.status_code = 201 if created else 200
    return {"item": _item_output(item, actor, "editor"), "created": created}


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
            .order_by(ReferenceItem.sort_order, ReferenceItem.label, ReferenceItem.id)
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
    _get(session, ReferenceSet, reference_id, lock=True)
    row = ReferenceItem(
        reference_set_id=reference_id,
        **{
            ("item_metadata" if key == "metadata" else key): value
            for key, value in data.model_dump().items()
        },
    )
    from app.registry.references import validate_item

    validate_item(session, row)
    session.add(row)
    _flush(session)
    emit(
        session,
        request,
        actor,
        "reference.item.created",
        "reference_item",
        row.id,
        {"reference_set_id": str(reference_id)},
    )
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
    _get(session, ReferenceSet, reference_id, lock=True)
    row = _get(session, ReferenceItem, item_id, lock=True)
    if row.reference_set_id != reference_id:
        fail(404, "not_found", "Объект не найден")
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is None and key not in {"parent_id", "valid_from", "valid_until"}:
            fail(422, "invalid_configuration", "Значение не может быть пустым")
        setattr(row, "item_metadata" if key == "metadata" else key, value)
    from app.registry.references import validate_item

    validate_item(session, row)
    _flush(session)
    emit(
        session,
        request,
        actor,
        "reference.item.updated",
        "reference_item",
        row.id,
        {"reference_set_id": str(reference_id)},
    )
    return _item_output(row, actor)


class ReferenceImport(Input):
    format: Literal["csv", "xlsx"]
    content: str = Field(max_length=2800000)


REFERENCE_COLUMNS = [
    "id",
    "code",
    "label",
    "description",
    "sort_order",
    "active",
    "parent_id",
    "valid_from",
    "valid_until",
]


@router.post("/references/{reference_id}/import")
def import_reference(
    reference_id: UUID,
    data: ReferenceImport,
    request: Request,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_admin),
):
    from pydantic import ValidationError

    from app.registry.references import validate_item
    from app.registry.tabular import read_tabular

    _get(session, ReferenceSet, reference_id, lock=True)
    try:
        content = base64.b64decode(data.content, validate=True)
        rows = read_tabular(data.format, content)
        if any(set(row) - set(REFERENCE_COLUMNS) for row in rows):
            raise ValueError("Unknown columns; provenance cannot be imported")
        created, updated, seen, pending = 0, 0, set(), []
        for values in rows:
            values = dict(values)
            item_id = values.pop("id", None)
            # Empty cells represent absent optional values, never source identifiers.
            for key in ("parent_id", "valid_from", "valid_until"):
                if key in values and values[key] in (None, ""):
                    values[key] = None
            if "sort_order" in values:
                values["sort_order"] = int(values["sort_order"] or 0)
            if "active" in values:
                boolean = str(values["active"]).lower()
                if boolean not in {"true", "false", "1", "0"}:
                    raise ValueError("Active must be true or false")
                values["active"] = boolean in {"true", "1"}
            values = {
                key: ("" if value is None and key in {"code", "description"} else value)
                for key, value in values.items()
            }
            parsed = ItemInput.model_validate(values)
            if item_id:
                identity = UUID(str(item_id))
                if identity in seen:
                    raise ValueError("Duplicate row identity")
                seen.add(identity)
                with session.no_autoflush:
                    row = _get(session, ReferenceItem, identity)
                if row.reference_set_id != reference_id:
                    raise ValueError("Item identity is outside this reference set")
                updated += 1
            else:
                row = ReferenceItem(id=uuid4(), reference_set_id=reference_id)
                session.add(row)
                created += 1
            for key, value in parsed.model_dump().items():
                if key == "metadata" and item_id:
                    continue
                setattr(row, "item_metadata" if key == "metadata" else key, value)
            pending.append(row)
        for row in pending:
            validate_item(session, row)
        _flush(session)
        for row in pending:
            emit(
                session,
                request,
                actor,
                "reference.item.updated" if row.id in seen else "reference.item.created",
                "reference_item",
                row.id,
                {"reference_set_id": str(reference_id)},
            )
        emit(session, request, actor, "reference.imported", "reference", reference_id)
        return {"created": created, "updated": updated}
    except (ValueError, ValidationError, binascii.Error):
        fail(422, "invalid_reference_import", "Некорректный файл справочника или значения строк")


@router.get("/references/{reference_id}/export")
def export_reference(
    reference_id: UUID,
    request: Request,
    format: Literal["csv", "xlsx"] = "csv",
    session=Depends(db_session, scope="function"),
    actor=Depends(current_admin),
):
    from app.registry.tabular import export_file

    _get(session, ReferenceSet, reference_id)
    rows = list(
        session.scalars(
            select(ReferenceItem)
            .where(ReferenceItem.reference_set_id == reference_id)
            .order_by(ReferenceItem.sort_order, ReferenceItem.label, ReferenceItem.id)
            .limit(2001)
        )
    )
    if len(rows) > 2000:
        fail(422, "reference_export_limit", "Экспорт ограничен 2000 строками")
    content = export_file(
        format,
        REFERENCE_COLUMNS,
        ([getattr(row, key) for key in REFERENCE_COLUMNS] for row in rows),
    )
    emit(session, request, actor, "reference.exported", "reference", reference_id)
    return Response(
        content,
        media_type="text/csv"
        if format == "csv"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="reference.{format}"'},
    )


@router.get("/references/{reference_id}/history")
def reference_history(
    reference_id: UUID,
    limit: int = 100,
    session=Depends(db_session, scope="function"),
    actor=Depends(current_admin),
):
    from sqlalchemy import or_

    from app.platform.models import AuditEvent

    _get(session, ReferenceSet, reference_id)
    if not 1 <= limit <= 200:
        fail(422, "invalid_limit", "Размер страницы от 1 до 200")
    rows = session.scalars(
        select(AuditEvent)
        .where(
            or_(
                AuditEvent.object_id == reference_id,
                AuditEvent.details["reference_set_id"].astext == str(reference_id),
            )
        )
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(limit)
    )
    return {
        "items": [
            {
                "id": row.id,
                "action": row.action,
                "occurred_at": row.created_at,
                "actor_id": row.actor_id,
                "object_id": row.object_id,
                "details": row.details,
            }
            for row in rows
        ]
    }
