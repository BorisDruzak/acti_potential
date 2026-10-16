from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from app.api.dependencies import current_actor, db_session
from app.api.errors import fail
from app.content.api import block_output
from app.content.models import ContentExtraction, DocumentBlock
from app.registry.documents import (
    document_summary,
    file_visible,
    get_document,
    get_schema,
    raw_values,
)
from app.registry.models import DocumentFile, DocumentVersion
from app.registry.policy import document_permissions
from app.relations import service
from app.relations.contracts import LABELS, DecisionInput, RelationInput, RelationType
from app.relations.models import DocumentRelation, RelationEvent

router = APIRouter()
Db = Annotated[object, Depends(db_session, scope="function")]
Actor = Annotated[object, Depends(current_actor)]
Mode = Literal["editor", "viewer"]
State = Literal["proposed", "confirmed", "rejected"]


@router.post("/document-relations", status_code=201)
def propose(data: RelationInput, request: Request, session: Db, actor: Actor):
    return service.propose(session, request, actor, data)


def get_relation(session, actor, identity, mode=None):
    relation = session.get(DocumentRelation, identity)
    if relation is None:
        fail(404, "relation_not_found", "Связь не найдена")
    resolved = service.endpoints(session, actor, relation, mode)
    return relation, resolved


@router.get("/document-relations/{relation_id}")
def detail(relation_id: UUID, session: Db, actor: Actor, mode: Mode | None = None):
    relation, resolved = get_relation(session, actor, relation_id, mode)
    return service.output(session, actor, relation, mode, resolved=resolved)


@router.post("/document-relations/{relation_id}/{action}")
def decision(
    relation_id: UUID,
    action: Literal["confirm", "reject"],
    data: DecisionInput,
    request: Request,
    session: Db,
    actor: Actor,
):
    return service.decide(
        session,
        request,
        actor,
        relation_id,
        data,
        "confirmed" if action == "confirm" else "rejected",
    )


@router.get("/document-relations/{relation_id}/basis")
def basis(
    relation_id: UUID,
    session: Db,
    actor: Actor,
    side: Literal["source", "target"] = "source",
    mode: Mode | None = None,
):
    relation, resolved = get_relation(session, actor, relation_id, mode)
    source, target, source_anchor, target_anchor = resolved
    document, anchor = (source, source_anchor) if side == "source" else (target, target_anchor)
    if document is None:
        return {
            "document": None,
            "anchor": None,
            "external_target": relation.external_target,
            "files": [],
            "blocks": [],
        }
    files, blocks = [], []
    if anchor:
        schema = get_schema(session, document)
        values = raw_values(document, schema)
        files = [
            {
                "id": file.id,
                "name": file.name,
                "version_id": file.version_id,
                "version": anchor["version"],
            }
            for file in session.scalars(
                select(DocumentFile).where(DocumentFile.version_id == anchor["version_id"])
            )
            if file_visible(schema, actor, file, values, mode)
        ]
        if anchor.get("block_id"):
            blocks = [block_output(session.get(DocumentBlock, anchor["block_id"]))]
    return {
        "document": document_summary(session, document, actor, mode=mode),
        "anchor": anchor,
        "external_target": None,
        "files": files,
        "blocks": blocks,
    }


@router.get("/documents/{document_id}/relation-options")
def options(document_id: UUID, session: Db, actor: Actor, mode: Mode | None = None):
    document = get_document(session, actor, document_id, mode=mode)
    permissions = document_permissions(session, actor, document)
    versions = {}
    if "download" in permissions:
        schema, values = (
            get_schema(session, document),
            raw_values(document, get_schema(session, document)),
        )
        for version, file in session.execute(
            select(DocumentVersion, DocumentFile)
            .join(DocumentFile, DocumentFile.version_id == DocumentVersion.id)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version.desc(), DocumentFile.created_at)
        ):
            if not file_visible(schema, actor, file, values, mode):
                continue
            version_output = versions.setdefault(
                version.id, {"id": version.id, "version": version.version, "files": []}
            )
            version_output["files"].append(
                {
                    "id": file.id,
                    "name": file.name,
                    "extractions": [
                        {"id": extraction.id, "state": extraction.state}
                        for extraction in session.scalars(
                            select(ContentExtraction)
                            .where(ContentExtraction.file_id == file.id)
                            .order_by(ContentExtraction.created_at.desc())
                        )
                    ],
                }
            )
    return {
        "versions": list(versions.values()),
        "permissions": {
            "propose": mode != "viewer" and "update" in permissions,
            "confirm": mode != "viewer" and {"update", "confirm_relations"} <= permissions,
        },
    }


@router.get("/documents/{document_id}/relations")
def relations(
    document_id: UUID,
    session: Db,
    actor: Actor,
    mode: Mode | None = None,
    relation_type: RelationType | None = Query(None, alias="type"),
    state: State | None = None,
    page: int = Query(1, ge=1, le=1000000),
    page_size: int = Query(20, ge=1, le=100),
):
    items, total, effects = [], 0, []
    for relation, resolved in service.visible_relations(
        session, actor, document_id, mode, relation_type=relation_type, state=state
    ):
        if (page - 1) * page_size <= total < page * page_size:
            items.append(
                service.output(
                    session, actor, relation, mode, resolved=resolved, document_id=document_id
                )
            )
        total += 1
    for relation, resolved in service.visible_relations(
        session, actor, document_id, mode, state="confirmed"
    ):
        if relation.target_document_id == document_id and service.applies_now(
            session, relation, resolved[1]
        ):
            if len(effects) <= 200:
                effects.append(
                    {
                        "relation_id": relation.id,
                        "relation_type": relation.relation_type,
                        "label": LABELS[relation.relation_type][1],
                        "effective_date": relation.effective_date,
                        "applies_now": True,
                        "source_document": document_summary(session, resolved[0], actor, mode=mode),
                        "target_version_id": relation.target_version_id,
                    }
                )
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "status_effects": effects[:200],
        "status_effects_truncated": len(effects) > 200,
    }


@router.get("/documents/{document_id}/relation-history")
def history(
    document_id: UUID,
    session: Db,
    actor: Actor,
    mode: Mode | None = None,
    page: int = Query(1, ge=1, le=1000000),
    page_size: int = Query(50, ge=1, le=100),
):
    visible = {
        relation.id: (relation, resolved)
        for relation, resolved in service.visible_relations(session, actor, document_id, mode)
    }
    events = session.scalars(
        select(RelationEvent)
        .where(RelationEvent.relation_id.in_(visible))
        .order_by(RelationEvent.created_at.desc(), RelationEvent.id)
        .execution_options(yield_per=100)
    )
    items, total = [], 0
    for event in events:
        if (page - 1) * page_size <= total < page * page_size:
            relation, resolved = visible[event.relation_id]
            items.append(
                {
                    "id": event.id,
                    "relation_id": relation.id,
                    "action": event.action,
                    "created_at": event.created_at,
                    "reason": event.reason,
                    "effective_date": event.effective_date,
                    "relation_type": relation.relation_type,
                    "source_document_id": relation.source_document_id,
                    "target_document_id": relation.target_document_id,
                    "actor_name": service.author(session, event.actor_id),
                    "applies_now": event.action == "confirmed"
                    and service.applies_now(session, relation, resolved[1]),
                }
            )
        total += 1
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/documents/{document_id}/relation-graph")
def graph(
    document_id: UUID,
    session: Db,
    actor: Actor,
    mode: Mode | None = None,
    relation_type: RelationType | None = Query(None, alias="type"),
    state: State | None = None,
    limit: int = Query(100, ge=1, le=200),
):
    seed = get_document(session, actor, document_id, mode=mode)
    nodes, edges, seen, frontier, visited, truncated = {}, [], set(), {document_id}, set(), False

    def add_node(document):
        summary = document_summary(session, document, actor, mode=mode)
        nodes[document.id] = {key: summary.get(key) for key in ("id", "title", "reg_number")}

    add_node(seed)
    while frontier and not truncated:
        current = frontier.pop()
        visited.add(current)
        for relation, resolved in service.visible_relations(
            session, actor, current, mode, relation_type=relation_type, state=state
        ):
            if relation.id in seen or relation.target_document_id is None:
                continue
            if len(edges) >= limit:
                truncated = True
                break
            seen.add(relation.id)
            source, target, source_anchor, target_anchor = resolved
            add_node(source)
            add_node(target)
            frontier |= {source.id, target.id} - visited
            edges.append(
                {
                    "id": relation.id,
                    "source_document_id": source.id,
                    "target_document_id": target.id,
                    "relation_type": relation.relation_type,
                    "state": relation.state,
                    "label": LABELS[relation.relation_type][0],
                    "inverse_label": LABELS[relation.relation_type][1],
                    "source_version_id": relation.source_version_id,
                    "target_version_id": relation.target_version_id,
                    "source_version": source_anchor["version"] if source_anchor else None,
                    "target_version": target_anchor["version"] if target_anchor else None,
                    "source_block_id": relation.source_block_id,
                    "target_block_id": relation.target_block_id,
                    "source_anchor": source_anchor,
                    "target_anchor": target_anchor,
                }
            )
    return {"nodes": list(nodes.values()), "edges": edges, "truncated": truncated}
