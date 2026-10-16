import hashlib
import json
from datetime import UTC, date, datetime
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException
from sqlalchemy import exists, or_, select

from app.api.errors import fail
from app.content.models import ContentExtraction, DocumentBlock
from app.identity.models import User
from app.registry.documents import (
    document_summary,
    file_visible,
    get_document,
    get_schema,
    raw_values,
)
from app.registry.events import emit
from app.registry.models import DocumentFile, DocumentVersion
from app.registry.policy import document_permissions
from app.relations.contracts import ACYCLIC_TYPES, EFFECT_TYPES, LABELS
from app.relations.models import DocumentRelation, RelationEvent


def graph_lock(session):
    from sqlalchemy import func

    session.execute(select(func.pg_advisory_xact_lock(731006)))


def anchor(session, actor, document_id, version_id=None, block_id=None, mode=None):
    block, file, version = None, None, None
    if block_id:
        block = session.get(DocumentBlock, block_id)
        extraction = session.get(ContentExtraction, block.extraction_id) if block else None
        file = session.get(DocumentFile, extraction.file_id) if extraction else None
        version = session.get(DocumentVersion, extraction.version_id) if extraction else None
    elif version_id:
        version = session.get(DocumentVersion, version_id)
    else:
        return None
    if version is None:
        fail(404, "relation_basis_not_found", "Основание недоступно")
    try:
        owner = get_document(session, actor, version.document_id, "download", mode=mode)
        schema = get_schema(session, owner)
        values = raw_values(owner, schema)
        files = [
            entry
            for entry in session.scalars(
                select(DocumentFile).where(DocumentFile.version_id == version.id)
            )
            if file_visible(schema, actor, entry, values, mode)
        ]
        if not files or file and file.id not in {entry.id for entry in files}:
            fail(404, "relation_basis_not_found", "Основание недоступно")
    except HTTPException as error:
        if error.status_code in {403, 404}:
            fail(404, "relation_basis_not_found", "Основание недоступно")
        raise
    if version.document_id != document_id or version_id and version.id != version_id:
        fail(422, "relation_basis_mismatch", "Версия или блок не принадлежат выбранному документу")
    result = {"version_id": version.id, "version": version.version}
    if block:
        result.update(
            file_id=file.id,
            file_name=file.name,
            block_id=block.id,
            ordinal=block.ordinal,
            page=block.page,
            anchor=block.anchor,
            snippet=block.text[:600],
        )
    return result


def endpoints(session, actor, relation, mode=None, *, action="read"):
    source = get_document(session, actor, relation.source_document_id, action, mode=mode)
    target = (
        get_document(session, actor, relation.target_document_id, action, mode=mode)
        if relation.target_document_id
        else None
    )
    source_anchor = anchor(
        session, actor, source.id, relation.source_version_id, relation.source_block_id, mode
    )
    target_anchor = (
        anchor(
            session, actor, target.id, relation.target_version_id, relation.target_block_id, mode
        )
        if target
        else None
    )
    return source, target, source_anchor, target_anchor


def can_confirm(session, actor, source, target, mode=None):
    return mode != "viewer" and all(
        {"update", "confirm_relations"} <= document_permissions(session, actor, document)
        for document in (source, target)
        if document is not None
    )


def applies_now(session, relation, target, today=None):
    if (
        relation.state != "confirmed"
        or relation.relation_type not in EFFECT_TYPES
        or target is None
    ):
        return False
    if relation.effective_date is None or relation.effective_date > (today or date.today()):
        return False
    if relation.target_version_id:
        version = session.get(DocumentVersion, relation.target_version_id)
        return bool(version and version.version == target.current_version)
    return True


def author(session, identity):
    person = session.get(User, identity) if identity else None
    return person.display_name if person else None


def output(session, actor, relation, mode=None, *, resolved=None, document_id=None):
    source, target, source_anchor, target_anchor = resolved or endpoints(
        session, actor, relation, mode
    )
    confirm = relation.state == "proposed" and can_confirm(session, actor, source, target, mode)
    return {
        **{
            key: getattr(relation, key)
            for key in (
                "id",
                "source_document_id",
                "target_document_id",
                "source_version_id",
                "target_version_id",
                "source_block_id",
                "target_block_id",
                "relation_type",
                "source",
                "state",
                "description",
                "external_target",
                "created_at",
                "decided_at",
                "reason",
                "effective_date",
            )
        },
        "source_document": document_summary(session, source, actor, mode=mode),
        "target_document": document_summary(session, target, actor, mode=mode) if target else None,
        "source_anchor": source_anchor,
        "target_anchor": target_anchor,
        "source_version": source_anchor["version"] if source_anchor else None,
        "target_version": target_anchor["version"] if target_anchor else None,
        "source_snippet": source_anchor.get("snippet") if source_anchor else None,
        "target_snippet": target_anchor.get("snippet") if target_anchor else None,
        "label": LABELS[relation.relation_type][0],
        "inverse_label": LABELS[relation.relation_type][1],
        "direction": "incoming"
        if document_id and relation.target_document_id == document_id
        else "outgoing",
        "created_by_name": author(session, relation.created_by),
        "decided_by_name": author(session, relation.decided_by),
        "permissions": {"confirm": confirm, "reject": confirm},
        "applies_now": applies_now(session, relation, target),
    }


def check_self_and_cycles(session, relation):
    if relation.source_document_id == relation.target_document_id:
        if (
            relation.relation_type != "previous_revision"
            or not relation.source_version_id
            or not relation.target_version_id
        ):
            fail(422, "relation_self", "Самоссылка разрешена только между явными редакциями")
        source = session.get(DocumentVersion, relation.source_version_id)
        target = session.get(DocumentVersion, relation.target_version_id)
        if source.version <= target.version:
            fail(422, "relation_version_order", "Предыдущая редакция должна быть старше исходной")
        return
    if relation.relation_type not in ACYCLIC_TYPES or relation.target_document_id is None:
        return
    predicates = (
        DocumentRelation.state.in_(["proposed", "confirmed"]),
        DocumentRelation.relation_type.in_(ACYCLIC_TYPES),
        DocumentRelation.target_document_id.is_not(None),
        DocumentRelation.source_document_id != DocumentRelation.target_document_id,
    )
    reachable = (
        select(DocumentRelation.target_document_id.label("identity"))
        .where(DocumentRelation.source_document_id == relation.target_document_id, *predicates)
        .cte("relation_reachable", recursive=True)
    )
    reachable = reachable.union(
        select(DocumentRelation.target_document_id)
        .join(reachable, DocumentRelation.source_document_id == reachable.c.identity)
        .where(*predicates)
    )
    if session.scalar(select(exists().where(reachable.c.identity == relation.source_document_id))):
        fail(409, "relation_cycle", "Связь создаёт недопустимый цикл нормативных оснований")


def record(session, request, actor, relation, action, reason=""):
    session.add(
        RelationEvent(
            relation_id=relation.id,
            action=action,
            actor_id=actor.id,
            reason=reason,
            effective_date=relation.effective_date,
        )
    )
    details = {"source_document_id": str(relation.source_document_id)}
    if relation.target_document_id:
        details["target_document_id"] = str(relation.target_document_id)
    emit(session, request, actor, "relation." + action, "document_relation", relation.id, details)


def propose(session, request, actor, data):
    graph_lock(session)
    values = data.model_dump(exclude={"external_target"})
    values["external_target"] = (
        data.external_target.model_dump(mode="json") if data.external_target else None
    )
    relation = DocumentRelation(**values, state="proposed", created_by=actor.id)
    source, target, source_anchor, target_anchor = endpoints(
        session, actor, relation, action="update"
    )
    if source_anchor:
        relation.source_version_id = source_anchor["version_id"]
    if target_anchor:
        relation.target_version_id = target_anchor["version_id"]
    identity = {
        key: str(getattr(relation, key)) if getattr(relation, key) else None
        for key in (
            "source_document_id",
            "target_document_id",
            "source_version_id",
            "target_version_id",
            "source_block_id",
            "target_block_id",
            "relation_type",
        )
    }
    if relation.external_target:
        external = relation.external_target
        parsed = urlsplit(external["url"])
        host = parsed.hostname.lower()
        if ":" in host:
            host = "[" + host + "]"
        authority = host + (":" + str(parsed.port) if parsed.port and parsed.port != 443 else "")
        identity["external_target"] = {
            "url": urlunsplit(
                ("https", authority, parsed.path or "/", parsed.query, parsed.fragment)
            ),
            **{key: external[key] for key in ("identifier", "version", "effective_date")},
        }
    else:
        identity["external_target"] = None
    relation.fingerprint = hashlib.sha256(
        json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    if session.scalar(
        select(DocumentRelation.id).where(DocumentRelation.fingerprint == relation.fingerprint)
    ):
        fail(409, "relation_duplicate", "Такая связь уже сохранена")
    check_self_and_cycles(session, relation)
    session.add(relation)
    session.flush()
    record(session, request, actor, relation, "proposed")
    return output(session, actor, relation, resolved=(source, target, source_anchor, target_anchor))


def decide(session, request, actor, identity, data, state):
    graph_lock(session)
    relation = session.scalar(
        select(DocumentRelation)
        .where(DocumentRelation.id == identity)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if relation is None:
        fail(404, "relation_not_found", "Связь не найдена")
    resolved = endpoints(session, actor, relation, action="update")
    if not can_confirm(session, actor, resolved[0], resolved[1]):
        fail(
            403, "relation_confirmation_forbidden", "Нет права подтверждать связи обоих документов"
        )
    if relation.state != "proposed":
        fail(409, "relation_decision_immutable", "Решение уже принято и неизменяемо")
    if state == "confirmed":
        check_self_and_cycles(session, relation)
    relation.state, relation.reason, relation.effective_date = (
        state,
        data.reason,
        data.effective_date,
    )
    relation.decided_by, relation.decided_at = actor.id, datetime.now(UTC)
    session.flush()
    record(session, request, actor, relation, state, data.reason)
    return output(session, actor, relation, resolved=resolved)


def visible_relations(session, actor, document_id, mode=None, *, relation_type=None, state=None):
    get_document(session, actor, document_id, mode=mode)
    query = select(DocumentRelation).where(
        or_(
            DocumentRelation.source_document_id == document_id,
            DocumentRelation.target_document_id == document_id,
        )
    )
    if relation_type:
        query = query.where(DocumentRelation.relation_type == relation_type)
    if state:
        query = query.where(DocumentRelation.state == state)
    for relation in session.scalars(
        query.order_by(DocumentRelation.created_at.desc(), DocumentRelation.id).execution_options(
            yield_per=100
        )
    ):
        try:
            resolved = endpoints(session, actor, relation, mode)
        except HTTPException as error:
            if error.status_code in {403, 404}:
                continue
            raise
        yield relation, resolved
