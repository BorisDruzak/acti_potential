"""One permission-aware query contract for document lists and exports."""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Date, DateTime, Numeric, and_, case, false, func, or_, select

from app.api.errors import fail
from app.registry.models import Document, FormSchema, Registry
from app.registry.policy import accessible_document_clause, field_readable, permissions_for
from app.registry.value_validation import uuid_value, validate_value


class SearchParameters(BaseModel):
    registry_id: UUID | None = None
    document_type_id: UUID | None = None
    q: str = Field("", max_length=255)
    reg_number: str | None = Field(None, max_length=255)
    title: str | None = Field(None, max_length=255)
    reg_date: date | None = None
    date_from: date | None = None
    date_to: date | None = None
    status: str | None = Field(None, max_length=100)
    card_state: Literal["draft", "published"] | None = None
    publication_target: Literal["internal", "shared"] | None = None
    filters: str | None = Field(None, max_length=8000)
    sort: str = Field("updated_at", max_length=110)
    direction: Literal["asc", "desc"] = "desc"
    page: int = Field(1, ge=1, le=1000000)
    page_size: int = Field(25, ge=1, le=100)
    mode: Literal["editor", "viewer"] | None = None


class ExportParameters(SearchParameters):
    format: Literal["csv", "xlsx"] = "csv"
    columns: str | None = Field(None, max_length=5000)


def fields_for(session, actor, mode=None, *, registry_id=None, document_type_id=None):
    allowed_types = {
        UUID(tid)
        for registry in session.scalars(select(Registry))
        if (registry_id is None or registry.id == registry_id)
        and "read" in permissions_for(session, actor, registry.id)
        for tid in registry.document_type_ids
    }
    accessible = select(Document.schema_version_id).where(accessible_document_clause(session, actor, mode))
    if registry_id:
        accessible = accessible.where(Document.registry_id == registry_id)
    query = select(FormSchema).where(
        FormSchema.state == "published",
        or_(FormSchema.document_type_id.in_(allowed_types), FormSchema.id.in_(accessible)),
    )
    if document_type_id:
        query = query.where(FormSchema.document_type_id == document_type_id)
    # Latest version first fixes labels/order deterministically while retaining
    # historical schemas for filtering and per-row export permissions.
    return [
        (row.id, field)
        for row in session.scalars(query.order_by(FormSchema.version.desc(), FormSchema.id))
        for section in row.schema_data.get("sections", [])
        for field in section.get("fields", [])
        if field_readable(field, actor, mode)
    ]


def field_expression(field):
    return getattr(Document, field["system"]) if field.get("system") else (
        Document.custom_data[field["key"]].astext
    )


def safe_system_filter(fields, system, predicate):
    return or_(false(), *[
        and_(Document.schema_version_id == sid, predicate(field_expression(field)))
        for sid, field in fields if field.get("system") == system
        and field.get("filterable", False) and not field.get("visible_when")
    ])


def custom_filter(session, actor, schema_id, field, value):
    kind = field["type"]
    if kind in {"group", "file"}:
        fail(422, "invalid_filter", "Поле недоступно для фильтрации")
    operator = "eq"
    if isinstance(value, dict):
        if set(value) - {"op", "value"} or "op" not in value:
            fail(422, "invalid_filter", "Неверный фильтр")
        operator, value = value["op"], value.get("value")
    if not isinstance(operator, str) or operator not in {
        "eq", "ne", "contains", "lt", "lte", "gt", "gte", "between", "in", "present", "absent"
    }:
        fail(422, "invalid_filter", "Неизвестный оператор фильтра")
    if operator in {"lt", "lte", "gt", "gte", "between"} and kind not in {
        "integer", "decimal", "date", "datetime"
    }:
        fail(422, "invalid_filter", "Диапазон недоступен для этого поля")
    expression = field_expression(field)
    if kind in {"integer", "decimal"}:
        expression = case((Document.schema_version_id == schema_id, expression)).cast(Numeric)
    elif kind in {"date", "datetime"} and not field.get("system"):
        expression = case((Document.schema_version_id == schema_id, expression)).cast(
            Date if kind == "date" else DateTime(timezone=True)
        )
    if operator == "present":
        predicate = expression.is_not(None)
        if kind in {"text", "textarea"}:
            predicate = and_(predicate, expression != "")
        if kind == "multi_reference":
            predicate = and_(predicate, field_expression(field) != "[]")
    elif operator == "absent":
        predicate = expression.is_(None)
        if kind in {"text", "textarea"}:
            predicate = or_(predicate, expression == "")
        if kind == "multi_reference":
            predicate = or_(predicate, field_expression(field) == "[]")
    else:
        def normalize(item):
            if isinstance(item, (dict, list)):
                fail(422, "invalid_filter", "Ожидается скалярное значение")
            if kind in {"reference", "multi_reference", "user", "department", "document"}:
                return str(uuid_value(item, field["key"])) if item is not None else None
            if kind == "integer" and isinstance(item, str):
                try:
                    item = int(item)
                except ValueError:
                    fail(422, "invalid_filter", "Ожидается целое число")
            normalized = validate_value(session, actor, {**field, "constraints": {}}, item, None)
            if normalized is None:
                return None
            if kind in {"integer", "decimal"}:
                return Decimal(str(normalized))
            if kind in {"date", "datetime"}:
                return (date.fromisoformat if kind == "date" else datetime.fromisoformat)(normalized)
            return str(normalized).lower() if isinstance(normalized, bool) else normalized

        if operator in {"between", "in"}:
            if not isinstance(value, list) or not 1 <= len(value) <= 100:
                fail(422, "invalid_filter", "Ожидается ограниченный список")
            normalized = [normalize(v) for v in value]
            if operator == "between":
                if len(normalized) != 2 or None in normalized or normalized[0] > normalized[1]:
                    fail(422, "invalid_filter", "Неверный диапазон")
                predicate = expression.between(*normalized)
            else:
                predicate = or_(*[
                    Document.custom_data[field["key"]].contains([v]) for v in normalized
                ]) if kind == "multi_reference" else expression.in_(normalized)
        else:
            normalized = normalize(value)
            if operator == "contains":
                if kind not in {"text", "textarea"} or normalized is None:
                    fail(422, "invalid_filter", "Поиск подстроки доступен для текста")
                predicate = expression.icontains(normalized, autoescape=True)
            elif operator in {"lt", "lte", "gt", "gte"}:
                if normalized is None:
                    fail(422, "invalid_filter", "Ожидается граница диапазона")
                predicate = {"lt": expression.__lt__, "lte": expression.__le__,
                             "gt": expression.__gt__, "gte": expression.__ge__}[operator](normalized)
            elif kind == "multi_reference" and normalized is not None:
                predicate = Document.custom_data[field["key"]].contains([normalized])
                if operator == "ne":
                    predicate = ~predicate
            else:
                predicate = expression != normalized if operator == "ne" else expression == normalized
    return and_(Document.schema_version_id == schema_id, predicate)


def search_query(session, actor, params):
    query = select(Document).where(accessible_document_clause(session, actor, params.mode))
    if params.card_state:
        query = query.where(Document.card_state == params.card_state)
    if params.publication_target:
        query = query.where(Document.publication_targets.contains([params.publication_target]))
    if params.registry_id:
        query = query.where(Document.registry_id == params.registry_id)
    if params.document_type_id:
        query = query.where(Document.document_type_id == params.document_type_id)
    fields = fields_for(session, actor, params.mode, registry_id=params.registry_id,
                        document_type_id=params.document_type_id)
    for system in ("reg_number", "title", "status"):
        value = getattr(params, system)
        if value is not None:
            query = query.where(safe_system_filter(fields, system, lambda col: col.contains(value)))
    if params.q:
        query = query.where(or_(
            safe_system_filter(fields, "title", lambda col: col.icontains(params.q, autoescape=True)),
            safe_system_filter(fields, "reg_number", lambda col: col.icontains(params.q, autoescape=True)),
        ))
    if params.date_from and params.date_to and params.date_from > params.date_to:
        fail(422, "invalid_filter", "Неверный диапазон дат")
    for value, operator in ((params.reg_date, "eq"), (params.date_from, "ge"), (params.date_to, "le")):
        if value:
            query = query.where(safe_system_filter(fields, "reg_date", lambda col: (
                col == value if operator == "eq" else col >= value if operator == "ge" else col <= value
            )))
    if params.filters:
        try:
            extra = json.loads(params.filters)
        except (ValueError, RecursionError):
            fail(422, "invalid_filter", "Неверный фильтр")
        if not isinstance(extra, dict) or len(extra) > 20:
            fail(422, "invalid_filter", "Неверный фильтр")
        for key, value in extra.items():
            schema_id = None
            if key.startswith("schema:"):
                try:
                    _, raw_schema_id, key = key.split(":", 2)
                    schema_id = UUID(raw_schema_id)
                except (ValueError, AttributeError):
                    fail(422, "invalid_filter", "Неверная версия схемы фильтра")
            options = [(sid, f) for sid, f in fields if f["key"] == key
                       and (schema_id is None or sid == schema_id)
                       and f.get("filterable") and not f.get("visible_when")]
            if not options:
                fail(422, "invalid_filter", "Поле недоступно для фильтрации")
            predicates = []
            for sid, field in options:
                try:
                    predicates.append(custom_filter(session, actor, sid, field, value))
                except HTTPException as error:
                    if error.status_code != 422:
                        raise
            if not predicates:
                fail(422, "invalid_filter", "Неверное значение фильтра")
            query = query.where(or_(*predicates))
    if params.sort in {"updated_at", "created_at", "reg_date", "reg_number", "title"}:
        order_column = getattr(Document, params.sort)
        if params.sort not in {"updated_at", "created_at"}:
            ids = [sid for sid, f in fields if f.get("system") == params.sort
                   and not f.get("visible_when")]
            order_column = case((Document.schema_version_id.in_(ids), order_column), else_=None)
    elif params.sort.startswith("custom:"):
        key = params.sort[7:]
        options = [(sid, f) for sid, f in fields if f["key"] == key and f.get("filterable")
                   and not f.get("visible_when") and f["type"] not in {"group", "file", "multi_reference"}]
        if not options:
            fail(422, "invalid_sort", "Поле недоступно для сортировки")
        kinds = {f["type"] for _, f in options}
        if len(kinds) != 1:
            fail(422, "invalid_sort", "Выберите тип документа для сортировки")
        order_column = case(*[(Document.schema_version_id == sid, field_expression(f))
                              for sid, f in options], else_=None)
        if next(iter(kinds)) in {"integer", "decimal"}:
            order_column = order_column.cast(Numeric)
        elif next(iter(kinds)) in {"date", "datetime"}:
            order_column = order_column.cast(
                Date if next(iter(kinds)) == "date" else DateTime(timezone=True)
            )
    else:
        fail(422, "invalid_sort", "Неизвестная сортировка")
    order = order_column.asc() if params.direction == "asc" else order_column.desc()
    return query.order_by(order.nulls_last(), Document.id), fields


def count_rows(session, query):
    return session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
