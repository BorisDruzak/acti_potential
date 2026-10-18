from copy import deepcopy
from datetime import UTC, date, datetime

from sqlalchemy import select

from app.api.errors import fail
from app.identity.models import User
from app.registry.models import (
    Document,
    DocumentFile,
    DocumentVersion,
    FormSchema,
    MetadataRevision,
)
from app.registry.policy import (
    accessible_document_clause,
    document_permissions,
    field_readable,
    redact_schema,
    redact_values,
)
from app.registry.presentation import configuration_schema
from app.registry.value_validation import SYSTEM_KEYS


def raw_values(document, schema):
    values = dict(document.custom_data)
    for section in schema.get("sections", []):
        for field in section.get("fields", []):
            system = field.get("system")
            if system in SYSTEM_KEYS:
                value = getattr(document, system)
                values[field["key"]] = value.isoformat() if isinstance(value, date) else value
    return values


def write_values(document, schema, values):
    custom = {}
    for section in schema.get("sections", []):
        for field in section.get("fields", []):
            key, system = field["key"], field.get("system")
            if system in SYSTEM_KEYS:
                value = values.get(key)
                if system == "reg_date" and value is not None:
                    value = date.fromisoformat(value)
                setattr(document, system, value)
            elif key in values:
                custom[key] = values[key]
    document.custom_data = custom
    document.updated_at = datetime.now(UTC)


def get_document(session, actor, document_id, action="read", *, lock=False, mode=None):
    query = select(Document).where(
        Document.id == document_id, accessible_document_clause(session, actor, mode)
    )
    if lock:
        query = query.with_for_update(of=Document).execution_options(populate_existing=True)
    document = session.scalar(query)
    if document is None:
        fail(404, "not_found", "Документ не найден")
    if action not in document_permissions(session, actor, document):
        fail(403, "forbidden", "Действие с документом не разрешено")
    return document


def get_schema(session, document):
    schema = session.get(FormSchema, document.schema_version_id)
    if schema is None or schema.state != "published":
        fail(409, "schema_unavailable", "Схема документа недоступна")
    return schema.schema_data


def historical_values(schema, values):
    """Project old scalar selections for today's form without changing history."""
    result = deepcopy(values)
    for section in schema.get("sections", []):
        for field in section.get("fields", []):
            key = field["key"]
            value = result.get(key)
            if field["type"] == "multi_reference" and value is not None and not isinstance(value, list):
                result[key] = [value]
            elif field["type"] == "group" and isinstance(value, list):
                nested = {"sections": [{"fields": field.get("children", [])}]}
                result[key] = [historical_values(nested, row) for row in value]
    return result


def check_revision(document, revision):
    if document.revision != revision:
        fail(
            409,
            "revision_conflict",
            "Документ уже изменён другим пользователем. Обновите карточку и повторите изменения.",
        )


def append_history(session, document, schema, actor):
    session.add(
        MetadataRevision(
            document_id=document.id,
            revision=document.revision,
            values=raw_values(document, schema),
            card_state=document.card_state,
            publication_targets=list(document.publication_targets),
            created_by=actor.id,
        )
    )


def summary(session, document, actor, schema=None):
    return {
        name: getattr(document, name)
        for name in (
            "id",
            "registry_id",
            "document_type_id",
            "schema_version_id",
            "revision",
            "current_version",
            "created_at",
            "updated_at",
            "card_state",
            "publication_targets",
        )
    }


def latest_editors(session, documents):
    """Read actual revision authors for a whole page, including imported cards without history."""
    identities = [document.id for document in documents]
    if not identities:
        return {}
    rows = session.execute(
        select(MetadataRevision.document_id, User.id, User.display_name)
        .join(User, User.id == MetadataRevision.created_by)
        .where(MetadataRevision.document_id.in_(identities))
        .distinct(MetadataRevision.document_id)
        .order_by(MetadataRevision.document_id, MetadataRevision.revision.desc())
    )
    return {document_id: {"id": identity, "display_name": name} for document_id, identity, name in rows}


def document_summary(session, document, actor, schema=None, mode=None, *, permissions=None, display_cache=None, editor_cache=None):
    schema = schema or get_schema(session, document)
    result = summary(session, document, actor, schema)
    editors = latest_editors(session, [document]) if editor_cache is None else editor_cache
    result["updated_by"] = editors.get(document.id)
    visible = redact_values(schema, raw_values(document, schema), actor, mode)
    systems = {
        f.get("system"): visible.get(f["key"])
        for s in schema.get("sections", [])
        for f in s.get("fields", [])
        if f.get("system") in SYSTEM_KEYS
    }
    result.update({k: systems.get(k) for k in SYSTEM_KEYS})
    result["values"] = visible
    from app.registry.document_export import display_values

    result["display_values"] = display_values(session, schema, visible, actor, mode, cache=display_cache)
    result["permissions"] = sorted(document_permissions(session, actor, document) if permissions is None else permissions)
    return result


def resolve_file_field(schema, path, values, *, historical=False):
    parts = path.split(".")
    field = next(
        (
            f
            for s in schema.get("sections", [])
            for f in s.get("fields", [])
            if f["key"] == parts[0]
        ),
        None,
    )
    if len(parts) == 1:
        return field, None, None
    if (
        len(parts) != 3
        or not parts[1].isascii()
        or not parts[1].isdecimal()
        or len(parts[1]) > 6
        or field is None
        or field["type"] != "group"
    ):
        return None, None, None
    index = int(parts[1])
    rows = values.get(parts[0]) or []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return None, None, None
    if index >= len(rows) and not historical:
        return None, None, None
    child = next((f for f in field.get("children", []) if f["key"] == parts[2]), None)
    return child, parts[0], index


def file_visible(schema, actor, file, values=None, mode=None):
    if file.field_key is None:
        return True
    field, parent, index = resolve_file_field(schema, file.field_key, values or {}, historical=True)
    if field is None or not field_readable(field, actor, mode):
        return False
    candidate = deepcopy(values or {})
    if parent:
        # Row deletion must not delete an immutable original's authorization
        # anchor. Group children use root conditions, so a structural probe suffices.
        candidate[parent] = [{field["key"]: []}]
        visible = redact_values(schema, candidate, actor, mode)
        return parent in visible and field["key"] in visible[parent][0]
    candidate[field["key"]] = []
    return field["key"] in redact_values(schema, candidate, actor, mode)


def files_for(session, document, actor, schema, mode=None):
    rows = session.execute(
        select(DocumentFile, DocumentVersion.version)
        .join(DocumentVersion, DocumentVersion.id == DocumentFile.version_id)
        .where(DocumentFile.document_id == document.id)
        .order_by(DocumentVersion.version)
    ).all()
    return [
        {
            **{
                key: getattr(file, key)
                for key in (
                    "id",
                    "name",
                    "size",
                    "mime",
                    "sha256",
                    "field_key",
                    "created_by",
                    "created_at",
                )
            },
            "version": version,
        }
        for file, version in rows
        if file_visible(schema, actor, file, raw_values(document, schema), mode)
    ]


def document_output(session, document, actor, mode=None):
    from app.numbering.models import NumberEntry

    schema = get_schema(session, document)
    values = redact_values(schema, raw_values(document, schema), actor, mode)
    visible_number = any(field.get("system") == "reg_number" and field["key"] in values
                         for section in schema.get("sections", []) for field in section.get("fields", []))
    entry_id = session.scalar(select(NumberEntry.id).where(
        NumberEntry.document_id == document.id,
        NumberEntry.number_string == document.reg_number,
    )) if visible_number else None
    return {
        **document_summary(session, document, actor, schema, mode),
        "values": values,
        "number_entry_id": entry_id,
        "number_locked": visible_number and document.card_state == "published",
        "schema": configuration_schema(redact_schema(schema, actor, mode)),
        "files": files_for(session, document, actor, schema, mode),
    }
