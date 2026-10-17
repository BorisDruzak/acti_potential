"""Version people fields for multiple selections without rewriting user history."""

import re
from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0014_people_selection"
down_revision = "0013_card_publication"
branch_labels = None
depends_on = None

PEOPLE_CODES = {"executors", "responsible_executors", "signers", "approvers"}


def clone_id(schema_id):
    return uuid5(NAMESPACE_URL, f"acti:people-selection:0014:{schema_id}")


def clean_label(value):
    if not isinstance(value, str):
        return value
    cleaned = re.sub(r"\bACTI[\s_-]*v\s*2\b", "", value, flags=re.I)
    if cleaned == value:
        return value
    return re.sub(r"\s+", " ", cleaned).strip(" —–:,-") or "Документы"


def selections(value):
    if value is None:
        return None
    entries = value if isinstance(value, list) else [value]
    result = []
    for entry in entries:
        if entry not in result:
            result.append(entry)
    return result


def people_field(field, reference_ids):
    # An explicit set takes precedence over a fallback code at runtime too.
    return field.get("type") in {"reference", "multi_reference"} and (
        str(field["reference_set_id"]) in reference_ids if field.get("reference_set_id")
        else field.get("reference_code") in PEOPLE_CODES
    )


def normalize_values(fields, values, reference_ids):
    result = deepcopy(values)
    if not isinstance(result, dict):
        return result
    for field in fields:
        key = field["key"]
        if key not in result:
            continue
        if people_field(field, reference_ids):
            result[key] = selections(result[key])
        elif field.get("type") == "group" and isinstance(result[key], list):
            result[key] = [normalize_values(field.get("children", []), row, reference_ids)
                           for row in result[key]]
    return result


def upgrade_schema(schema, reference_ids):
    result = deepcopy(schema)
    fields = [f for section in result.get("sections", []) for f in section.get("fields", [])]
    converted = {f["key"] for f in fields
                 if f.get("type") == "reference" and people_field(f, reference_ids)}

    def update_fields(items):
        for field in items:
            for key in ("label", "description"):
                if key in field:
                    field[key] = clean_label(field[key])
            if people_field(field, reference_ids):
                field["type"] = "multi_reference"
                if "default" in field:
                    field["default"] = selections(field["default"])
            for key in ("visible_when", "required_when"):
                condition = field.get(key)
                if condition and condition.get("field") in converted and "value" in condition:
                    if condition.get("op") in {"eq", "ne"}:
                        condition["value"] = selections(condition["value"])
                    elif condition.get("op") in {"in", "not_in"}:
                        condition["value"] = [selections(v) for v in condition["value"]]
            if field.get("type") == "group":
                children = field.get("children", [])
                if isinstance(field.get("default"), list):
                    field["default"] = [normalize_values(children, row, reference_ids)
                                        for row in field["default"]]
                update_fields(children)

    update_fields(fields)
    for section in result.get("sections", []):
        if "title" in section:
            section["title"] = clean_label(section["title"])
    for column in result.get("list_view", {}).get("columns", []):
        if "label" in column:
            column["label"] = clean_label(column["label"])
    return result


def upgrade():
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE form_schemas, documents IN SHARE ROW EXCLUSIVE MODE"))
    schemas = sa.table("form_schemas", sa.column("id", sa.Uuid()),
                       sa.column("document_type_id", sa.Uuid()), sa.column("version", sa.Integer()),
                       sa.column("state", sa.String()), sa.column("schema", JSONB()))
    documents = sa.table("documents", sa.column("id", sa.Uuid()),
                         sa.column("schema_version_id", sa.Uuid()), sa.column("custom_data", JSONB()))
    fragments = sa.table("content_metadata_fragments", sa.column("id", sa.Uuid()),
                         sa.column("document_id", sa.Uuid()), sa.column("label", sa.String()))
    reference_ids = {str(row.id) for row in connection.execute(sa.text(
        "SELECT id FROM reference_sets WHERE code IN ('executors','responsible_executors','signers','approvers')"
    ))}
    original_schemas = list(connection.execute(sa.select(schemas).order_by(
        schemas.c.document_type_id, schemas.c.version
    )).mappings())
    identities = {row["id"] for row in original_schemas}
    upgraded_schemas = {row["id"]: upgrade_schema(row["schema"], reference_ids)
                        for row in original_schemas}
    changed_types = {row["document_type_id"] for row in original_schemas
                     if row["state"] == "published"
                     and upgraded_schemas[row["id"]] != row["schema"]
                     and clone_id(row["id"]) not in identities}
    latest_published = {}
    versions = {}
    for row in original_schemas:
        versions[row["document_type_id"]] = row["version"]
        if row["state"] == "published":
            latest_published[row["document_type_id"]] = row["id"]
    for row in original_schemas:
        upgraded = upgraded_schemas[row["id"]]
        target = row["id"]
        # Cloning an older version must not make its obsolete layout the default.
        # Preserve the latest published layout as the last newly published version.
        keep_latest = (row["document_type_id"] in changed_types
                       and latest_published.get(row["document_type_id"]) == row["id"])
        if upgraded != row["schema"] or keep_latest:
            if row["state"] == "published":
                target = clone_id(row["id"])
                if target not in identities:
                    versions[row["document_type_id"]] += 1
                    connection.execute(schemas.insert().values(
                        id=target, document_type_id=row["document_type_id"],
                        version=versions[row["document_type_id"]], state="published", schema=upgraded,
                    ))
                    identities.add(target)
            else:
                connection.execute(schemas.update().where(schemas.c.id == target).values(schema=upgraded))
        fields = [f for section in upgraded.get("sections", []) for f in section.get("fields", [])]
        for document in connection.execute(sa.select(documents).where(
            documents.c.schema_version_id == row["id"]
        )).mappings():
            normalized = normalize_values(fields, document["custom_data"], reference_ids)
            if target != row["id"] or normalized != document["custom_data"]:
                # This is a representation upgrade, not an edit by the original author.
                # Preserve revision, updated_at, history, source, ACL, files and outbox.
                connection.execute(documents.update().where(documents.c.id == document["id"]).values(
                    schema_version_id=target, custom_data=normalized,
                ))
            for fragment in connection.execute(sa.select(fragments).where(
                fragments.c.document_id == document["id"]
            )).mappings():
                label = clean_label(fragment["label"])
                if label != fragment["label"]:
                    connection.execute(fragments.update().where(fragments.c.id == fragment["id"]).values(label=label))
    connection.execute(sa.text(
        "UPDATE document_types SET name='Нормативные и распорядительные документы' "
        "WHERE code='acti-v2' AND name='ACTI v2 — нормативные и распорядительные документы'"
    ))
    connection.execute(sa.text(
        "UPDATE reference_sets SET name='Типы документов' "
        "WHERE code='document_types' AND name='Типы записей ACTI v2'"
    ))


def downgrade():
    identities = set(op.get_bind().scalars(sa.text("SELECT id FROM form_schemas")))
    if any(clone_id(identity) in identities for identity in identities):
        raise RuntimeError(
            "People selection configuration has been upgraded; restore a pre-upgrade backup "
            "instead of discarding multiple selections or immutable published schemas"
        )
