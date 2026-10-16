"""Bounded, batched serialization of the selected authorized page."""

from uuid import UUID

from sqlalchemy import select

from app.identity.models import User
from app.registry.documents import document_summary, raw_values
from app.registry.models import (
    Document,
    DocumentACL,
    DocumentFile,
    FormSchema,
    ReferenceItem,
    RegistryAccess,
)
from app.registry.policy import accessible_document_clause, permissions_for, redact_values


def summaries(session, actor, documents, mode):
    if not documents:
        return {}
    identities = [d.id for d in documents]
    schemas = {
        s.id: s
        for s in session.scalars(
            select(FormSchema).where(FormSchema.id.in_({d.schema_version_id for d in documents}))
        )
    }
    grants = list(
        session.scalars(
            select(RegistryAccess).where(
                RegistryAccess.registry_id.in_({d.registry_id for d in documents}),
                RegistryAccess.user_id == actor.id,
            )
        )
    )
    permissions = {
        grant.registry_id: permissions_for(session, actor, grant.registry_id) for grant in grants
    }
    restricted = set(
        session.scalars(
            select(DocumentACL.document_id)
            .where(DocumentACL.document_id.in_(identities))
            .distinct()
        )
    )
    personal = {
        a.document_id: set(a.permissions)
        for a in session.scalars(
            select(DocumentACL).where(
                DocumentACL.document_id.in_(identities), DocumentACL.user_id == actor.id
            )
        )
    }
    refs, users, files, linked = set(), set(), set(), set()

    def collect(fields, values):
        for field in fields:
            value = values.get(field["key"])
            if value is None:
                continue
            kind = field["type"]
            if kind == "group":
                for row in value:
                    collect(field.get("children", []), row)
            elif kind in {"reference", "multi_reference", "department", "user", "file", "document"}:
                target = (
                    users
                    if kind == "user"
                    else files
                    if kind == "file"
                    else linked
                    if kind == "document"
                    else refs
                )
                for identity in value if isinstance(value, list) else [value]:
                    target.add(UUID(str(identity)))

    for document in documents:
        schema = schemas[document.schema_version_id].schema_data
        visible = redact_values(schema, raw_values(document, schema), actor, mode)
        collect([f for s in schema["sections"] for f in s["fields"]], visible)
    cache = {}
    for item in session.scalars(select(ReferenceItem).where(ReferenceItem.id.in_(refs))):
        for kind in ("reference", "multi_reference", "department"):
            cache[(kind, str(item.id))] = item.label
    for user in session.scalars(select(User).where(User.id.in_(users))):
        cache[("user", str(user.id))] = user.display_name
    for file in session.scalars(select(DocumentFile).where(DocumentFile.id.in_(files))):
        cache[("file", str(file.id))] = file.name
    related = list(
        session.scalars(
            select(Document).where(
                Document.id.in_(linked), accessible_document_clause(session, actor)
            )
        )
    )
    related_schemas = (
        {
            s.id: s
            for s in session.scalars(
                select(FormSchema).where(FormSchema.id.in_({d.schema_version_id for d in related}))
            )
        }
        if related
        else {}
    )
    for document in related:
        schema = related_schemas[document.schema_version_id].schema_data
        visible = redact_values(schema, raw_values(document, schema), actor, mode)
        title = next(
            (
                visible[f["key"]]
                for s in schema["sections"]
                for f in s["fields"]
                if f.get("system") == "title" and visible.get(f["key"])
            ),
            str(document.id),
        )
        cache[("document", str(document.id))] = title
    result = {}
    for document in documents:
        permitted = permissions.get(document.registry_id, set())
        if document.id in restricted:
            permitted = permitted & personal.get(document.id, set())
        schema = schemas[document.schema_version_id].schema_data
        item = document_summary(
            session, document, actor, schema, mode, permissions=permitted, display_cache=cache
        )
        if actor.scoped_operations is not None and not actor.scoped_operations.intersection(
            {"files", "content"}
        ):

            def omit(fields, values):
                for field in fields:
                    if field["type"] == "file":
                        values.pop(field["key"], None)
                    elif field["type"] == "group":
                        for row in values.get(field["key"], []):
                            omit(field.get("children", []), row)

            for values in (item["values"], item["display_values"]):
                omit([f for s in schema["sections"] for f in s["fields"]], values)
        result[document.id] = item
    return result
