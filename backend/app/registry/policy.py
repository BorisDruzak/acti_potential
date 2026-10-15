from copy import deepcopy
from decimal import Decimal, InvalidOperation

from sqlalchemy import and_, exists, or_, select

from app.registry.models import Document, DocumentACL, RegistryAccess

CONTENT_ACTIONS = frozenset({"read", "create", "update", "upload", "download"})
ACTIONS = CONTENT_ACTIONS | {"manage"}


def role_actions(actor):
    if actor.roles & {"admin", "editor", "user"}:
        return CONTENT_ACTIONS
    if "viewer" in actor.roles:
        return frozenset({"read", "download"})
    return frozenset()


def permissions_for(session, actor, registry_id) -> set[str]:
    row = session.get(RegistryAccess, (registry_id, actor.id))
    return set(row.permissions if row else []) & role_actions(actor)


def document_permissions(session, actor, document) -> set[str]:
    permissions = permissions_for(session, actor, document.registry_id)
    restricted = session.scalar(select(exists().where(DocumentACL.document_id == document.id)))
    if restricted:
        acl = session.get(DocumentACL, (document.id, actor.id))
        permissions &= set(acl.permissions if acl else [])
    return permissions


def accessible_document_clause(session, actor):
    if "read" not in role_actions(actor):
        return Document.id.is_(None)
    registry = exists().where(
        RegistryAccess.registry_id == Document.registry_id,
        RegistryAccess.user_id == actor.id,
        RegistryAccess.permissions.contains(["read"]),
    )
    restricted = exists().where(DocumentACL.document_id == Document.id)
    individual = exists().where(
        DocumentACL.document_id == Document.id,
        DocumentACL.user_id == actor.id,
        DocumentACL.permissions.contains(["read"]),
    )
    return and_(registry, or_(~restricted, individual))


def field_readable(field, actor, mode=None):
    mode = mode or ("editor" if actor.roles & {"admin", "editor", "user"} else "viewer")
    if not actor.roles & {"admin", "editor", "user"}:
        mode = "viewer"
    effective_roles = actor.roles | ({"editor"} if "user" in actor.roles else set())
    roles = field.get("read_roles") or []
    modes = field.get("modes") or []
    if roles and not effective_roles.intersection(roles):
        return False
    if mode is not None and modes and mode not in modes:
        return False
    return True


def field_writable(field, actor):
    roles = field.get("write_roles") or []
    effective_roles = actor.roles | ({"editor"} if "user" in actor.roles else set())
    return (
        field_readable(field, actor, "editor")
        and field.get("editable", True)
        and (not roles or bool(effective_roles.intersection(roles)))
    )


def field_accessible(field, actor, fields, mode=None, *, values=None, seen=frozenset()):
    """Resolve the complete visibility dependency chain before exposing any value."""
    if id(field) in seen or not field_readable(field, actor, mode):
        return False
    condition = field.get("visible_when")
    if not condition:
        return True
    dependency = fields.get(condition["field"])
    if dependency is None or not field_accessible(
        dependency, actor, fields, mode, values=values, seen=seen | {id(field)}
    ):
        return False
    return values is None or condition_matches(condition, values, fields)


def redact_schema(schema, actor, mode=None, *, _root_fields=None):
    result = deepcopy(schema)
    root_fields = _root_fields or {
        f["key"]: f for s in schema.get("sections", []) for f in s.get("fields", [])
    }
    visible = set()
    for section in result.get("sections", []):
        section["fields"] = [f for f in section.get("fields", []) if field_readable(f, actor, mode)]
        for field in section["fields"]:
            visible.add(field["key"])
            if field.get("children"):
                nested = redact_schema(
                    {"sections": [{"fields": field["children"]}]},
                    actor,
                    mode,
                    _root_fields=root_fields,
                )
                field["children"] = nested["sections"][0]["fields"]
            field["editable"] = field_writable(field, actor)
            if field.get("visible_when"):
                field["filterable"] = False
    for section in result.get("sections", []):
        for field in section["fields"]:
            for key in ("visible_when", "required_when"):
                condition = field.get(key)
                dependency = root_fields.get(condition["field"]) if condition else None
                if condition and (
                    not dependency or not field_accessible(dependency, actor, root_fields, mode)
                ):
                    # A hidden dependency cannot disclose either values or its predicate.
                    field.pop(key, None)
                    field["editable"] = False
    return result


def condition_matches(condition, values, fields=None):
    if not condition:
        return True
    value, expected = values.get(condition["field"]), condition.get("value")
    op = condition["op"]

    def equal(other):
        if isinstance(value, bool) or isinstance(other, bool):
            return type(value) is type(other) and value == other
        dependency = (fields or {}).get(condition["field"], {})
        if dependency.get("type") == "decimal" and value is not None and other is not None:
            try:
                actual, target = Decimal(str(value)), Decimal(str(other))
                return actual.is_finite() and target.is_finite() and actual == target
            except InvalidOperation:
                return False
        return value == other

    if op == "eq":
        return equal(expected)
    if op == "ne":
        return not equal(expected)
    if op == "in":
        return any(equal(item) for item in expected) if isinstance(expected, list) else False
    if op == "present":
        return value is not None and value != "" and value != []
    return False


def redact_values(schema, values, actor, mode=None, *, _root_values=None, _root_fields=None):
    output = {}
    root_values = values if _root_values is None else _root_values
    root_fields = _root_fields or {
        f["key"]: f for s in schema.get("sections", []) for f in s.get("fields", [])
    }
    for section in schema.get("sections", []):
        for field in section.get("fields", []):
            if not field_accessible(field, actor, root_fields, mode, values=root_values):
                continue
            if field["key"] not in values:
                continue
            value = values[field["key"]]
            if field["type"] == "group" and isinstance(value, list):
                nested = {"sections": [{"fields": field.get("children", [])}]}
                value = [
                    redact_values(
                        nested, row, actor, mode, _root_values=root_values, _root_fields=root_fields
                    )
                    for row in value
                ]
            output[field["key"]] = value
    return output
