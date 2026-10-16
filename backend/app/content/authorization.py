"""Compile the existing declarative field policy into fail-closed SQL predicates.

Only the schema catalogue is read in Python, never a list of corpus documents.
Every visibility dependency is itself authorized, exactly as field_accessible.
"""

from sqlalchemy import Numeric, and_, exists, false, func, literal, or_, select, true
from sqlalchemy.dialects.postgresql import JSONB

from app.registry.document_search import field_expression
from app.registry.models import Document, DocumentACL, FormSchema, RegistryAccess
from app.registry.policy import field_accessible, field_readable, role_actions


def field_clause(field, fields, actor, mode, seen=frozenset()):
    key = id(field)
    if key in seen or not field_readable(field, actor, mode):
        return false()
    condition = field.get("visible_when")
    if not condition:
        return true()
    dependency = fields.get(condition["field"])
    if dependency is None:
        return false()
    permitted = field_clause(dependency, fields, actor, mode, seen | {key})
    column = field_expression(dependency)
    value = (
        func.to_jsonb(column, type_=JSONB)
        if dependency.get("system")
        else Document.custom_data[dependency["key"]]
    )
    expected = condition.get("value")
    op = condition["op"]

    def equal(target):
        if target is None:
            return or_(value.is_(None), value == literal(None, type_=JSONB))
        if dependency["type"] == "decimal":
            if isinstance(target, (bool, list, dict)) or target == "":
                return false()
            return func.coalesce(column.cast(Numeric) == target, False)
        return func.coalesce(value == literal(target, type_=JSONB), False)

    if op in {"eq", "ne"}:
        predicate = equal(expected)
        if op == "ne":
            predicate = ~predicate
    elif op in {"in", "not_in"}:
        predicate = or_(false(), *[equal(item) for item in expected])
        if op == "not_in":
            predicate = ~predicate
    else:
        predicate = and_(value.is_not(None), ~equal(None), ~equal(""), ~equal([]))
        if op == "absent":
            predicate = ~func.coalesce(predicate, False)
    return and_(permitted, predicate)


def source_clauses(session, actor, mode, key_column, *, files=False):
    branches, coarse = [], [key_column.is_(None)] if files else []
    for schema in session.scalars(select(FormSchema).where(FormSchema.state == "published")):
        fields = {
            f["key"]: f for s in schema.schema_data.get("sections", []) for f in s.get("fields", [])
        }
        allowed = [key_column.is_(None)] if files else []
        for field in fields.values():
            if not files and field.get("searchable") is False:
                continue
            permission = field_clause(field, fields, actor, mode)
            statically_allowed = field_accessible(field, actor, fields, mode)
            if field["type"] == "group":
                for child in field.get("children", []):
                    if not files and child.get("searchable") is False:
                        continue
                    if (child["type"] == "file") == files:
                        path = f"^{field['key']}\\.[0-9]+\\.{child['key']}$"
                        if statically_allowed and field_accessible(child, actor, fields, mode):
                            coarse.append(key_column.op("~")(path))
                        allowed.append(
                            and_(
                                key_column.op("~")(path),
                                permission,
                                field_clause(child, fields, actor, mode),
                            )
                        )
            elif (field["type"] == "file") == files:
                allowed.append(and_(key_column == field["key"], permission))
                if statically_allowed:
                    coarse.append(key_column == field["key"])
        branches.append(and_(Document.schema_version_id == schema.id, or_(false(), *allowed)))
    return and_(or_(false(), *coarse), or_(false(), *branches))


def download_clause(actor):
    if "download" not in role_actions(actor) or (
        actor.scoped_operations is not None
        and not actor.scoped_operations.intersection({"files", "content"})
    ):
        return false()
    registry = exists().where(
        RegistryAccess.registry_id == Document.registry_id,
        RegistryAccess.user_id == actor.id,
        RegistryAccess.permissions.contains(["download"]),
    )
    restricted = exists().where(DocumentACL.document_id == Document.id)
    individual = exists().where(
        DocumentACL.document_id == Document.id,
        DocumentACL.user_id == actor.id,
        DocumentACL.permissions.contains(["download"]),
    )
    return and_(registry, or_(~restricted, individual))
