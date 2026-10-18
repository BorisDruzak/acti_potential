"""Authorized display labels and bounded batch document export."""

import json
from datetime import date, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException, Response

from app.api.errors import fail
from app.registry.document_search import count_rows, search_query
from app.registry.documents import get_document, get_schema, raw_values
from app.registry.events import emit
from app.registry.models import Document, DocumentFile, FormSchema, ReferenceItem
from app.registry.policy import field_readable, redact_values
from app.registry.presentation import configuration_text
from app.registry.tabular import export_file

MAX_EXPORT_ROWS = 10000
MAX_EXPORT_TEXT = 16 * 1024 * 1024


def display_values(session, schema, values, actor, mode=None, *, exporting=False, cache=None):
    def display(field, value):
        if value is None:
            return None
        kind = field["type"]
        if kind == "group":
            nested = {"sections": [{"fields": field.get("children", [])}]}
            return [display_values(session, nested, row, actor, mode, exporting=exporting, cache=cache)
                    for row in value]
        if kind in {"reference", "multi_reference", "department"}:
            def label(identifier):
                if cache is not None:
                    return cache.get((kind, str(identifier)), "Не указано" if exporting else str(identifier))
                item = session.get(ReferenceItem, UUID(str(identifier)))
                return item.label if item else ("Не указано" if exporting else str(identifier))
            return [label(v) for v in value] if kind == "multi_reference" else label(value)
        if kind == "user":
            if cache is not None:
                return cache.get((kind, str(value)), "Не указано" if exporting else str(value))
            from app.identity.models import User

            user = session.get(User, UUID(str(value)))
            return user.display_name if user else ("Не указано" if exporting else str(value))
        if kind == "document":
            if cache is not None:
                return cache.get((kind, str(value)), "Недоступный документ")
            try:
                linked = get_document(session, actor, UUID(str(value)), mode=mode)
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
            if cache is not None:
                return [cache[(kind, str(identifier))] for identifier in value if (kind, str(identifier)) in cache]
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


def export_documents_response(request, session, actor, params, *, document_ids=None, document_query=None):
    query, fields = search_query(session, actor, params)
    if document_query is not None:
        query = document_query
    if document_ids is not None:
        query = query.where(Document.id.in_(document_ids))
    total = count_rows(session, query)
    if total > MAX_EXPORT_ROWS:
        fail(422, "export_limit", "Экспорт ограничен 10000 строками. Уточните фильтры.")
    schemas = {sid: session.get(FormSchema, sid).schema_data for sid in dict.fromkeys(sid for sid, _ in fields)}
    allowed = {}
    for sid, field in fields:
        if field.get("exportable", False):
            allowed.setdefault(field["key"], configuration_text(field["label"]))
    # The versioned list view determines presentation order and custom labels.
    configured = []
    for schema in schemas.values():
        configured.extend(schema.get("list_view", {}).get("columns", []))
    ordered = {}
    for column in configured:
        if column["key"] in allowed:
            ordered.setdefault(column["key"], configuration_text(column.get("label") or allowed[column["key"]]))
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
    excel = params.format == "xlsx"
    headers = [ordered[key] if excel else f"{ordered[key]} [{key}]" for key in keys]
    field_types = {field["key"]: field for _, field in reversed(fields)}
    widths = [68 if field_types[key].get("system") == "title" or field_types[key]["type"] == "textarea"
              else 18 if field_types[key]["type"] in {"date", "datetime", "boolean", "integer", "decimal"}
              else max(22, min(40, len(ordered[key]) + 6)) for key in keys]

    def excel_value(field, value):
        if value is None:
            return None
        if field["type"] in {"date", "datetime"}:
            try:
                parsed = (datetime.fromisoformat if field["type"] == "datetime" else date.fromisoformat)(str(value))
                # Excel has no timezone support: retain the original offset in text.
                return str(value) if isinstance(parsed, datetime) and parsed.tzinfo else parsed
            except ValueError:
                return str(value)
        if field["type"] == "group":
            children = {child["key"]: child for child in field.get("children", [])}
            return "\n".join("; ".join(
                f"{configuration_text(children[key]['label'])}: {excel_value(children[key], item)}"
                for key, item in row.items() if key in children and item is not None
            ) for row in value)
        if isinstance(value, list):
            return "\n".join(str(item) for item in value)
        if isinstance(value, bool):
            return "Да" if value else "Нет"
        return value

    def rows():
        consumed = 0
        with session.scalars(query.limit(MAX_EXPORT_ROWS + 1).execution_options(yield_per=200)) as result:
            for index, document in enumerate(result):
                if index >= MAX_EXPORT_ROWS:
                    fail(422, "export_limit", "Экспорт ограничен 10000 строками")
                schema = schemas.get(document.schema_version_id) or get_schema(session, document)
                visible = redact_values(schema, raw_values(document, schema), actor, params.mode)
                displayed = display_values(session, schema, visible, actor, params.mode, exporting=True)
                row_fields = {f["key"]: f for section in schema.get("sections", []) for f in section.get("fields", [])}
                row = []
                for key in keys:
                    value = displayed.get(key)
                    if excel and key in row_fields:
                        value = excel_value(row_fields[key], value)
                    elif isinstance(value, (dict, list)):
                        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                    if isinstance(value, bool):
                        value = "Да" if value else "Нет"
                    consumed += len(str(value or "").encode("utf-8"))
                    if consumed > MAX_EXPORT_TEXT:
                        fail(422, "export_limit", "Экспорт превышает 16 MiB. Уточните фильтры.")
                    row.append(value)
                yield row

    try:
        content = export_file(params.format, headers, rows(), presentation=excel, widths=widths)
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
