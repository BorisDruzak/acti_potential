"""Narrow compatibility with the immutable people-selection upgrade from 0014."""

from copy import deepcopy
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select

from app.registry.models import FormSchema, ReferenceSet
from app.registry.presentation import configuration_schema

PEOPLE_CODES = frozenset({"executors", "responsible_executors", "signers", "approvers"})


def _selections(value):
    if value is None:
        return None
    result = []
    for item in value if isinstance(value, list) else [value]:
        if item not in result:
            result.append(item)
    return result


def _people_field(field, reference_ids):
    return field.get("type") in {"reference", "multi_reference"} and (
        str(field["reference_set_id"]) in reference_ids if field.get("reference_set_id")
        else field.get("reference_code") in PEOPLE_CODES
    )


def _normalize_defaults(fields, values, reference_ids):
    result = deepcopy(values)
    if not isinstance(result, dict):
        return result
    for field in fields:
        key = field["key"]
        if key not in result:
            continue
        if _people_field(field, reference_ids):
            result[key] = _selections(result[key])
        elif field.get("type") == "group" and isinstance(result[key], list):
            result[key] = [_normalize_defaults(field.get("children", []), row, reference_ids)
                           for row in result[key]]
    return result


def _people_schema_0014(schema, reference_ids):
    """Compare complete content, including ACL/conditions; never infer from IDs alone.

    Keep this representation aligned with frozen migration 0014. Later unrelated
    published versions must not acquire compatibility with an earlier process.
    """
    result = configuration_schema(schema)
    fields = [f for section in result.get("sections", []) for f in section.get("fields", [])]
    converted = {f["key"] for f in fields
                 if f.get("type") == "reference" and _people_field(f, reference_ids)}

    def transform(items):
        for field in items:
            if _people_field(field, reference_ids):
                field["type"] = "multi_reference"
                if "default" in field:
                    field["default"] = _selections(field["default"])
            for key in ("visible_when", "required_when"):
                condition = field.get(key)
                if condition and condition.get("field") in converted and "value" in condition:
                    if condition.get("op") in {"eq", "ne"}:
                        condition["value"] = _selections(condition["value"])
                    elif condition.get("op") in {"in", "not_in"}:
                        condition["value"] = [_selections(value) for value in condition["value"]]
            if field.get("type") == "group":
                children = field.get("children", [])
                if isinstance(field.get("default"), list):
                    field["default"] = [_normalize_defaults(children, row, reference_ids)
                                        for row in field["default"]]
                transform(children)

    transform(fields)
    return result


def compatible_people_schema(session, current_id, pinned_id):
    """Accept equality or the exact published 0014 clone, never arbitrary versions."""
    try:
        current_id, pinned_id = UUID(str(current_id)), UUID(str(pinned_id))
    except (ValueError, TypeError, AttributeError):
        return False
    if current_id == pinned_id:
        return True
    if current_id != uuid5(NAMESPACE_URL, f"acti:people-selection:0014:{pinned_id}"):
        return False
    current, pinned = session.get(FormSchema, current_id), session.get(FormSchema, pinned_id)
    if (current is None or pinned is None or current.state != "published"
            or pinned.state != "published" or current.document_type_id != pinned.document_type_id):
        return False
    reference_ids = {str(identity) for identity in session.scalars(
        select(ReferenceSet.id).where(ReferenceSet.code.in_(PEOPLE_CODES))
    )}
    return current.schema_data == _people_schema_0014(pinned.schema_data, reference_ids)


def pinned_scalar_values(schema, current_schema, values):
    """Project singleton selections for a frozen scalar form; reject lossy flattening."""
    result = deepcopy(values)
    if not isinstance(result, dict):
        return result
    current_fields = {field["key"]: field for section in current_schema.get("sections", [])
                      for field in section.get("fields", [])}
    for section in schema.get("sections", []):
        for field in section.get("fields", []):
            key, value = field["key"], result.get(field["key"])
            current = current_fields.get(key, {})
            if (field["type"] == "reference" and current.get("type") == "multi_reference"
                    and isinstance(value, list)):
                if len(value) > 1:
                    raise ValueError("Закреплённая форма результата допускает одного сотрудника")
                result[key] = value[0] if value else None
            elif field["type"] == "group" and isinstance(value, list):
                nested = {"sections": [{"fields": field.get("children", [])}]}
                current_nested = {"sections": [{"fields": current.get("children", [])}]}
                result[key] = [pinned_scalar_values(nested, current_nested, row) for row in value]
    return result
