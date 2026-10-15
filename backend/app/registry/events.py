from app.api.middleware import source_address
from app.platform.audit import AuditInput, record_audit
from app.platform.outbox import enqueue_event


def emit(session, request, actor, action, object_type, object_id, details=None):
    details = details or {}
    record_audit(
        session,
        AuditInput(
            actor.id,
            action,
            object_type,
            object_id,
            details,
            request.state.request_id,
            source_address(request),
        ),
    )
    enqueue_event(session, action, object_id, details)
