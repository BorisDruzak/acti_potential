"""Authorized display labels and bounded batch document export."""

import json
from uuid import UUID, uuid4

from fastapi import HTTPException, Response

from app.api.errors import fail
from app.registry.document_search import count_rows, search_query
from app.registry.documents import get_document, get_schema, raw_values
from app.registry.events import emit
from app.registry.models import Document, DocumentFile, FormSchema, ReferenceItem
from app.registry.policy import field_readable, redact_values
from app.registry.tabular import export_file

MAX_EXPORT_ROWS = 10000
MAX_EXPORT_TEXT = 16 * 1024 * 1024


def display_values(session, schema, values, actor, mode=None, *, exporting=False):
    def display(field, value):
        if value is None:
            return None
        kind = field["type"]
        if kind == "group":
            nested = {"sections": [{"fields": field.get("children", [])}]}
            return [display_values(session, nested, row, actor, mode, exporting=exporting)
                    for row in value]
        if kind in {"reference", "multi_reference", "department"}:
            def label(identifier):
                item = session.get(ReferenceItem, UUID(str(identifier)))
                return item.label if item else str(identifier)
            return [label(v) for v in value] if kind == "multi_reference" else label(value)
        if kind == "user":
            from app.identity.models import User

            user = session.get(User, UUID(str(value)))
            return user.display_name if user else str(value)
        if kind == "document":
            try:
                linked = get_document(session, actor, UUID(str(value)))
                linked_schema = get_schema(session, linked)
                permitted = redact_values(linked_schema, raw_values(linked, linked_schema), actor, mode)
                for section in linked_schema.get("sections", []):
                    for entry in section.get("fields", []):
                        if entry.get("system") == "title" and permitted.get(entry["key"]):
                            return permitted[entry["key"]]
                return str(linked.id)
            except HTTPException:
                return "Недоступный документ"
        if kind == "file":
            names = []
            for identifier in value:
                item = session.get(DocumentFile, UUID(str(identifier)))
                if item:
                    names.append(item.name)
            return names
        return value

    return {
        field["key"]: display(field, values[field["key"]])
        for section in schema.get("sections", []) for field in section.get("fields", [])
        if field["key"] in values and field_readable(field, actor, mode)
        and (not exporting or field.get("exportable", False))
    }


def export_documents_response(request, session, actor, params, *, document_ids=None):
    query, fields = search_query(session, actor, params)
    if document_ids is not None:
        query = query.where(Document.id.in_(document_ids))
    total = count_rows(session, query)
    if total > MAX_EXPORT_ROWS:
        fail(422, "export_limit", "Экспорт ограничен 10000 строками. Уточните фильтры.")
    schemas = {sid: session.get(FormSchema, sid).schema_data for sid in dict.fromkeys(sid for sid, _ in fields)}
    allowed = {}
    for sid, field in fields:
        if field.get("exportable", False):
            allowed.setdefault(field["key"], field["label"])
    # The versioned list view determines presentation order and custom labels.
    configured = []
    for schema in schemas.values():
        configured.extend(schema.get("list_view", {}).get("columns", []))
    ordered = {}
    for column in configured:
        if column["key"] in allowed:
            ordered.setdefault(column["key"], column.get("label") or allowed[column["key"]])
    for key, label in allowed.items():
        ordered.setdefault(key, label)
    if params.columns:
        keys = params.columns.split(",")
        if len(keys) != len(set(keys)) or any(k not in allowed for k in keys):
            fail(422, "invalid_columns", "Колонка недоступна для экспорта")
    else:
        keys = list(ordered)
    if not keys:
        fail(422, "invalid_columns", "Нет разрешённых экспортируемых колонок")
    headers = [f"{ordered[key]} [{key}]" for key in keys]

    def rows():
        consumed = 0
        with session.scalars(query.limit(MAX_EXPORT_ROWS + 1).execution_options(yield_per=200)) as result:
            for index, document in enumerate(result):
                if index >= MAX_EXPORT_ROWS:
                    fail(422, "export_limit", "Экспорт ограничен 10000 строками")
                schema = schemas.get(document.schema_version_id) or get_schema(session, document)
                visible = redact_values(schema, raw_values(document, schema), actor, params.mode)
                displayed = display_values(session, schema, visible, actor, params.mode, exporting=True)
                row = []
                for key in keys:
                    value = displayed.get(key)
                    if isinstance(value, (dict, list)):
                        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                    if isinstance(value, bool):
                        value = "Да" if value else "Нет"
                    consumed += len(str(value or "").encode("utf-8"))
                    if consumed > MAX_EXPORT_TEXT:
                        fail(422, "export_limit", "Экспорт превышает 16 MiB. Уточните фильтры.")
                    row.append(value)
                yield row

    try:
        content = export_file(params.format, headers, rows())
    except ValueError:
        fail(422, "export_value", "Значение превышает ограничения формата экспорта")
    emit(session, request, actor, "document.exported", "document_export", uuid4(),
         {"registry_id": str(params.registry_id)} if params.registry_id else {})
    media = "text/csv; charset=utf-8" if params.format == "csv" else (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return Response(content, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="acti-documents.{params.format}"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    })
