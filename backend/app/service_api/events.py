"""Current-authority event pointers; bookmarks never cache authorization decisions."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import delete, func, select

from app.api.errors import fail
from app.content.models import ContentExtraction
from app.execution.models import StepExecution, StepResult
from app.knowledge.models import PracticeCase, PracticeLink
from app.platform.contracts import validate_payload
from app.platform.models import OutboxEvent
from app.processes import service as processes
from app.registry.models import DocumentFile
from app.relations.models import DocumentRelation
from app.service_api import retrieval
from app.service_api.auth import operation
from app.service_api.contracts import EventPage
from app.service_api.models import ServiceEventCursor
from app.service_api.retrieval import Context, Db

router = APIRouter()
PREFIX = "/api/service/v1"


def pointer(kind, identity, path):
    return {"type": kind, "id": identity, "href": PREFIX + path}


def resolve_event(session, context, event):
    """Unknown, obsolete, or partly inaccessible origin sets are omitted wholesale."""
    from fastapi import HTTPException

    try:
        if event.payload_version != 1:
            return None
        validate_payload(event.event_type, event.payload)
        origins = []

        def document(identity):
            operation(context, "documents")
            retrieval.get_document(session, context.actor, UUID(str(identity)))
            item = pointer("document", identity, f"/documents/{identity}")
            origins.append(item)
            return item

        def source(value):
            document(value["document_id"])
            resolved = processes.basis(session, context.actor, value, "viewer")
            if value.get("version_id"):
                operation(context, "files")
                retrieval.file_version(UUID(str(value["version_id"])), session, context)
                origins.append(
                    pointer(
                        "file_version", value["version_id"], f"/file-versions/{value['version_id']}"
                    )
                )
            if value.get("block_id"):
                operation(context, "content")
                retrieval.block(UUID(str(value["block_id"])), session, context)
                origins.append(pointer("block", value["block_id"], f"/blocks/{value['block_id']}"))
            return resolved

        def knowledge_version(identity):
            operation(context, "knowledge")
            obj, version = retrieval.knowledge.get_version(
                session, context.actor, UUID(str(identity))
            )
            source(version.data["original"])
            binding(version.data)
            return pointer("template_version", identity, f"/template-versions/{identity}")

        def binding(data):
            if data.get("process_version_id"):
                origins.append(process_version(UUID(str(data["process_version_id"]))))

        def process_version(identity):
            operation(context, "processes")
            process, version = processes.get_version(session, context.actor, identity)
            steps, _ = processes.graph(session, version)
            for step in steps:
                for origin in [*step.get("sources", []), *step.get("templates", [])]:
                    source(origin)
                for template_id in step.get("template_versions", []):
                    # Template metadata bindings may point back to this process; avoid recursion.
                    operation(context, "knowledge")
                    _, template = retrieval.knowledge.get_version(
                        session, context.actor, UUID(str(template_id))
                    )
                    source(template.data["original"])
                    origins.append(
                        pointer(
                            "template_version", template_id, f"/template-versions/{template_id}"
                        )
                    )
            return pointer("process_version", version.id, f"/process-versions/{version.id}")

        name, identity = event.event_type, event.object_id
        if name.startswith("form.schema."):
            retrieval.form_schema(identity, session, context)
            obj = pointer("form_schema", identity, f"/form-schemas/{identity}")
        elif name.startswith("document.") and name != "document.exported":
            if name == "document.file.downloaded":
                operation(context, "files")
                file = session.get(DocumentFile, identity)
                if file is None:
                    return None
                retrieval.allowed_file(session, context, file.id)
                document(file.document_id)
                obj = pointer("file", file.id, f"/files/{file.id}")
            else:
                obj = document(identity)
        elif name.startswith("content.extraction."):
            operation(context, "content")
            row = session.get(ContentExtraction, identity)
            if row is None:
                return None
            file = retrieval.allowed_file(session, context, row.file_id)
            document(file.document_id)
            obj = pointer("extraction", row.id, f"/extractions/{row.id}/blocks")
        elif name.startswith("relation."):
            operation(context, "relations")
            row = session.get(DocumentRelation, identity)
            if row is None:
                return None
            retrieval.relation_data(session, context, row)
            source(
                {
                    "document_id": str(row.source_document_id),
                    "version_id": str(row.source_version_id) if row.source_version_id else None,
                    "block_id": str(row.source_block_id) if row.source_block_id else None,
                }
            )
            if row.target_document_id:
                source(
                    {
                        "document_id": str(row.target_document_id),
                        "version_id": str(row.target_version_id) if row.target_version_id else None,
                        "block_id": str(row.target_block_id) if row.target_block_id else None,
                    }
                )
            obj = pointer("relation", row.id, f"/relations/{row.id}")
        elif name.startswith("process."):
            version_id = event.payload.get("process_version_id")
            if not version_id:
                return None
            obj = process_version(UUID(str(version_id)))
        elif name.startswith("knowledge.template."):
            version_id = event.payload.get("template_version_id")
            if not version_id:
                return None
            obj = knowledge_version(version_id)
        elif name.startswith("knowledge.case."):
            operation(context, "knowledge")
            case = retrieval.knowledge.get_object(session, context.actor, PracticeCase, identity)
            if case.state != "published":
                return None
            binding(case.data)
            for link in session.scalars(
                select(PracticeLink).where(PracticeLink.case_id == identity)
            ):
                source(link.data)
            obj = pointer("case", identity, f"/cases/{identity}")
        elif name.startswith("number."):
            operation(context, "numbers")
            from app.numbering.models import NumberEntry

            entry = session.get(NumberEntry, identity)
            if entry is None:
                retrieval.number_series(identity, session, context)
                obj = pointer("number_series", identity, f"/number-series/{identity}")
            else:
                retrieval.number_data(session, context, entry)
                if entry.document_id:
                    document(entry.document_id)
                obj = pointer("number_entry", identity, f"/number-entries/{identity}")
        elif name.startswith("execution."):
            operation(context, "execution")
            instance = retrieval.execution.get_instance(session, context.actor, identity)
            if retrieval.execution.trajectory_restricted(
                session, context.actor, instance, "viewer"
            ):
                return None
            for step in instance.snapshot["steps"]:
                for origin in [*step.get("sources", []), *step.get("templates", [])]:
                    source(origin)
                for template_id in step.get("template_versions", []):
                    origins.append(knowledge_version(template_id))
            for result in session.scalars(
                select(StepResult)
                .join(StepExecution, StepExecution.id == StepResult.step_execution_id)
                .where(StepExecution.instance_id == identity)
            ):
                for origin in result.attachments:
                    source(origin)
                if result.result_document:
                    document(result.result_document["document_id"])
            obj = pointer("execution_instance", identity, f"/execution/instances/{identity}")
        else:
            return None
        unique = {
            (value["type"], str(value["id"])): value
            for value in origins
            if (value["type"], str(value["id"])) != (obj["type"], str(obj["id"]))
        }
        return {
            "id": event.id,
            "event_type": name,
            "payload_version": 1,
            "created_at": event.created_at,
            "object": obj,
            "origins": list(unique.values()),
        }
    except (HTTPException, ValueError, KeyError, TypeError):
        return None


def scope_hash(context):
    material = (
        ",".join(sorted(context.actor.scoped_registry_ids))
        + "|"
        + ",".join(sorted(context.actor.scoped_operations))
    )
    return hashlib.sha256(material.encode()).hexdigest()


@router.get("/events", response_model=EventPage)
def events(
    session: Db,
    context: Context,
    cursor: UUID | None = None,
    page_size: int = Query(20, ge=1, le=100),
):
    operation(context, "events")
    now = datetime.now(UTC)
    fingerprint = scope_hash(context)
    previous = session.get(ServiceEventCursor, cursor) if cursor else None
    if cursor and (
        previous is None
        or previous.token_id != context.token.id
        or previous.scope_hash != fingerprint
        or previous.expires_at <= now
    ):
        fail(403, "service_cursor_rejected", "Закладка недоступна или истекла")
    if (
        session.scalar(
            select(func.count())
            .select_from(ServiceEventCursor)
            .where(
                ServiceEventCursor.token_id == context.token.id, ServiceEventCursor.expires_at > now
            )
        )
        >= 2000
    ):
        fail(429, "service_cursor_limit", "Слишком много действующих закладок; повторите позже")
    after_ordinal = previous.after_ordinal if previous else 0
    query = (
        select(OutboxEvent)
        .where(OutboxEvent.state == "processed", OutboxEvent.delivery_ordinal > after_ordinal)
        .order_by(OutboxEvent.delivery_ordinal)
        .limit(500)
    )
    items = []
    for event in session.scalars(query):
        after_ordinal = event.delivery_ordinal
        item = resolve_event(session, context, event)
        if item:
            items.append(item)
            if len(items) == page_size:
                break
    bookmark = ServiceEventCursor(
        token_id=context.token.id,
        scope_hash=fingerprint,
        after_ordinal=after_ordinal,
        expires_at=min(context.token.expires_at, now + timedelta(minutes=15)),
    )
    session.add(bookmark)
    session.flush()
    return {"items": items, "next_cursor": bookmark.id}


def cleanup_cursors(factory, limit=500):
    with factory.begin() as session:
        expired = (
            select(ServiceEventCursor.id)
            .where(ServiceEventCursor.expires_at <= datetime.now(UTC))
            .order_by(ServiceEventCursor.expires_at)
            .limit(limit)
        )
        session.execute(delete(ServiceEventCursor).where(ServiceEventCursor.id.in_(expired)))
