"""Scoped operational diagnostics. Unknown event objects are deliberately unavailable."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.api.account_admin import audit
from app.api.errors import fail
from app.content.models import ContentExtraction
from app.execution.models import ExecutionEvent, ProcessInstance
from app.knowledge import service as knowledge
from app.knowledge.models import KnowledgeUsage, PracticeCase, Template
from app.numbering import service as numbering
from app.numbering.models import NumberSeries
from app.platform.models import OutboxEvent
from app.registry.models import Document, DocumentFile
from app.registry.policy import accessible_document_clause
from app.service_api.admin import Admin, Db, delegable
from app.service_api.auth import ServiceContext
from app.service_api.contracts import MetricsRead, Page, QueueRead, Reason
from app.service_api.events import resolve_event
from app.service_api.models import ApiUsageBucket

router = APIRouter()


def diagnostic_context(session, actor, registry_id=None):
    from dataclasses import replace

    from app.service_api.contracts import OPERATIONS

    scopes = delegable(session, actor)
    if registry_id is not None:
        if registry_id not in scopes:
            fail(404, "registry_not_found", "Реестр не найден")
        scopes = {registry_id: scopes[registry_id]}
    scoped = replace(
        actor,
        scoped_registry_ids=frozenset(str(value) for value in scopes),
        scoped_operations=frozenset(OPERATIONS),
    )
    return ServiceContext(scoped, SimpleNamespace(), SimpleNamespace())


def queue_rows(session, context, state):
    query = (
        select(OutboxEvent)
        .where(OutboxEvent.state == state)
        .order_by(OutboxEvent.created_at, OutboxEvent.id)
        .execution_options(yield_per=200)
    )
    for row in session.scalars(query):
        if resolve_event(session, context, row) is not None:
            yield row


def queue_output(row):
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "event_type",
            "payload_version",
            "state",
            "attempts",
            "created_at",
            "error_code",
        )
    }


@router.get("/admin/operations/queue", response_model=Page[QueueRead])
def queue(
    session: Db,
    actor: Admin,
    state: str = Query("failed", pattern="^(pending|failed)$"),
    registry_id: UUID | None = None,
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(20, ge=1, le=100),
):
    context = diagnostic_context(session, actor, registry_id)
    items, total, offset = [], 0, (page - 1) * page_size
    for row in queue_rows(session, context, state):
        if offset <= total < offset + page_size:
            items.append(queue_output(row))
        total += 1
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/admin/operations/queue/{event_id}/retry", response_model=QueueRead)
def retry(event_id: UUID, data: Reason, request: Request, session: Db, actor: Admin):
    context = diagnostic_context(session, actor)
    row = session.scalar(select(OutboxEvent).where(OutboxEvent.id == event_id).with_for_update())
    if row is None or resolve_event(session, context, row) is None:
        fail(404, "queue_event_not_found", "Событие недоступно")
    if row.state != "failed":
        fail(409, "queue_event_not_failed", "Повтор доступен только для ошибочного события")
    row.state, row.attempts, row.error_code, row.next_attempt_at = (
        "pending",
        0,
        None,
        datetime.now(UTC),
    )
    audit(
        session,
        request,
        actor,
        "service.queue.retried",
        row.id,
        {"reason": data.reason},
        object_type="outbox_event",
    )
    return queue_output(row)


@router.get("/admin/operations/metrics", response_model=MetricsRead)
def metrics(session: Db, actor: Admin, registry_id: UUID | None = None):
    context = diagnostic_context(session, actor, registry_id)
    scoped = context.actor
    registry_ids = [UUID(value) for value in scoped.scoped_registry_ids]
    documents = select(Document.id).where(accessible_document_clause(session, scoped))
    document_count = session.scalar(select(func.count()).select_from(documents.subquery()))
    file_ids = []
    from fastapi import HTTPException

    from app.service_api.retrieval import allowed_file

    for identity in session.scalars(
        select(DocumentFile.id).where(DocumentFile.document_id.in_(documents))
    ):
        try:
            allowed_file(session, context, identity)
            file_ids.append(identity)
        except HTTPException:
            pass
    extraction_counts = dict(
        session.execute(
            select(ContentExtraction.state, func.count())
            .where(ContentExtraction.file_id.in_(file_ids))
            .group_by(ContentExtraction.state)
        ).all()
    )
    parser_errors = [
        {"error_code": code, "count": count}
        for code, count in session.execute(
            select(ContentExtraction.error_code, func.count())
            .where(
                ContentExtraction.file_id.in_(file_ids), ContentExtraction.error_code.is_not(None)
            )
            .group_by(ContentExtraction.error_code)
            .order_by(ContentExtraction.error_code)
        )
    ]
    visible_knowledge = [
        obj.id
        for model in (Template, PracticeCase)
        for obj in knowledge.visible_objects(session, scoped, model)
    ]
    actions = dict(
        session.execute(
            select(KnowledgeUsage.action, func.sum(KnowledgeUsage.count))
            .where(
                KnowledgeUsage.registry_id.in_(registry_ids),
                KnowledgeUsage.object_id.in_(visible_knowledge),
            )
            .group_by(KnowledgeUsage.action)
        ).all()
    )
    buckets = session.execute(
        select(
            ApiUsageBucket.operation,
            func.sum(ApiUsageBucket.requests),
            func.sum(ApiUsageBucket.duration_ms_total),
        )
        .where(ApiUsageBucket.registry_id.in_(registry_ids))
        .group_by(ApiUsageBucket.operation)
    ).all()
    requests, duration, opens, open_duration = 0, 0, 0, 0
    for operation, count, elapsed in buckets:
        requests += count
        duration += elapsed
        if operation == "time_to_open":
            opens += count
            open_duration += elapsed
        if operation in {"process_open", "service.process_open"}:
            actions["process_open"] = actions.get("process_open", 0) + count
    instances = select(ProcessInstance.id).where(ProcessInstance.registry_id.in_(registry_ids))
    instance_count = session.scalar(select(func.count()).select_from(instances.subquery()))
    actions["process_started"] = session.scalar(
        select(func.count())
        .select_from(ExecutionEvent)
        .where(ExecutionEvent.instance_id.in_(instances), ExecutionEvent.action == "started")
    )
    visible_numbers = 0
    for series in session.scalars(
        select(NumberSeries).where(NumberSeries.registry_id.in_(registry_ids))
    ):
        try:
            visible_numbers += sum(1 for _ in numbering.visible_entries(session, scoped, series))
        except HTTPException:
            pass
    return {
        "content": {
            "documents": document_count,
            "files": len(file_ids),
            "extractions": extraction_counts,
            "parser_errors": parser_errors,
        },
        "usage": [{"action": key, "count": value} for key, value in sorted(actions.items())],
        "numbers": {"visible_entries": visible_numbers},
        "execution": {"visible_instances": instance_count},
        "queue": {
            state: sum(1 for _ in queue_rows(session, context, state))
            for state in ("pending", "failed")
        },
        "retrieval": {
            "requests": requests,
            "duration_ms_total": duration,
            "time_to_open_count": opens,
            "time_to_open_ms_total": open_duration,
        },
    }
