"""Scoped search diagnostics and controlled rebuild over the existing worker backlog."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.api.dependencies import current_admin, db_session
from app.api.errors import fail
from app.content.authorization import download_clause, source_clauses
from app.content.models import ContentExtraction, DocumentIndexState
from app.platform.models import AuditEvent
from app.registry.models import Document, DocumentFile, RegistryAccess
from app.registry.policy import accessible_document_clause, permissions_for
from app.service_api.models import ApiUsageBucket

router = APIRouter()
Db = Annotated[object, Depends(db_session, scope="function")]
Admin = Annotated[object, Depends(current_admin)]


def record_search(session, actor, params, duration_ms):
    registries = list(
        session.scalars(
            select(RegistryAccess.registry_id).where(
                RegistryAccess.user_id == actor.id,
                RegistryAccess.permissions.contains(["read"]),
                *(
                    [RegistryAccess.registry_id.in_(params.registry_ids)]
                    if params.registry_ids
                    else []
                ),
                *(
                    [RegistryAccess.registry_id.in_(actor.scoped_registry_ids)]
                    if actor.scoped_registry_ids is not None
                    else []
                ),
            )
        )
    )
    bucket = next((bound for bound in (250, 500, 1000, 2000, 8000) if duration_ms <= bound), 8001)
    if not registries:
        return
    statement = insert(ApiUsageBucket).values(
        [
            dict(
                id=uuid4(),
                day=datetime.now(UTC).date(),
                registry_id=rid,
                operation=f"search.lexical.le_{bucket}",
                requests=1,
                duration_ms_total=duration_ms,
            )
            for rid in registries
        ]
    )
    session.execute(
        statement.on_conflict_do_update(
            constraint="uq_api_usage_bucket",
            set_={
                "requests": ApiUsageBucket.requests + 1,
                "duration_ms_total": ApiUsageBucket.duration_ms_total + duration_ms,
            },
        )
    )


def stale():
    return or_(
        DocumentIndexState.document_id.is_(None),
        DocumentIndexState.document_revision != Document.revision,
        DocumentIndexState.index_version != 2,
    )


@router.get("/admin/search/status")
def status(session: Db, actor: Admin, registry_id: UUID | None = None):
    clauses = [accessible_document_clause(session, actor)]
    if registry_id:
        clauses.append(Document.registry_id == registry_id)
    documents = select(Document.id).where(*clauses)
    total = session.scalar(select(func.count()).select_from(documents.subquery()))
    pending = session.scalar(
        select(func.count())
        .select_from(Document)
        .outerjoin(DocumentIndexState, DocumentIndexState.document_id == Document.id)
        .where(*clauses, stale())
    )
    file_query = (
        select(ContentExtraction.state, func.count(), func.min(ContentExtraction.created_at))
        .join(Document, Document.id == ContentExtraction.document_id)
        .join(DocumentFile, DocumentFile.id == ContentExtraction.file_id)
        .where(
            *clauses,
            download_clause(actor),
            source_clauses(session, actor, None, DocumentFile.field_key, files=True),
        )
        .group_by(ContentExtraction.state)
    )
    states, oldest = {}, None
    for state, count, created in session.execute(file_query):
        states[state] = count
        if state in {"pending", "running"} and (oldest is None or created < oldest):
            oldest = created
    allowed_registries = select(Document.registry_id).where(*clauses).distinct()
    samples = list(
        session.execute(
            select(
                ApiUsageBucket.operation,
                func.sum(ApiUsageBucket.requests),
                func.sum(ApiUsageBucket.duration_ms_total),
            )
            .where(
                ApiUsageBucket.registry_id.in_(allowed_registries),
                ApiUsageBucket.operation.like("search.lexical.%"),
            )
            .group_by(ApiUsageBucket.operation)
        )
    )
    requests = sum(r[1] for r in samples)
    accumulated, p95 = 0, None
    for action, count, _ in sorted(samples, key=lambda row: int(row[0].rsplit("_", 1)[1])):
        accumulated += count
        if accumulated >= requests * 0.95 and p95 is None:
            p95 = int(action.rsplit("_", 1)[1])
    last = session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.action == "search.rebuild.requested",
            AuditEvent.object_id.in_(allowed_registries),
        )
        .order_by(AuditEvent.created_at.desc())
        .limit(1)
    )
    return {
        "indexed_documents": total - pending,
        "pending_documents": pending,
        "extractions": states,
        "queue_age_seconds": int((datetime.now(UTC) - oldest).total_seconds()) if oldest else 0,
        "last_rebuild": {"at": last.created_at, "state": "running" if pending else "completed"}
        if last
        else None,
        "latency": {
            "requests": requests,
            "mean_ms": round(sum(r[2] for r in samples) / requests) if requests else None,
            "p95_upper_bound_ms": p95,
        },
    }


class RebuildInput(BaseModel):
    registry_id: UUID
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/admin/search/rebuild", status_code=202)
def rebuild(data: RebuildInput, request: Request, session: Db, actor: Admin):
    if not data.reason.strip() or not {"read", "update"} <= permissions_for(
        session, actor, data.registry_id
    ):
        fail(403, "rebuild_forbidden", "Перестройка индекса не разрешена")
    from app.api.account_admin import audit

    identities = select(Document.id).where(
        Document.registry_id == data.registry_id, accessible_document_clause(session, actor)
    )
    session.execute(
        update(DocumentIndexState)
        .where(DocumentIndexState.document_id.in_(identities))
        .values(index_version=0)
    )
    audit(
        session,
        request,
        actor,
        "search.rebuild.requested",
        data.registry_id,
        {"reason": data.reason},
        object_type="registry",
    )
    return {"state": "queued", "registry_id": data.registry_id}
