from dataclasses import dataclass
from uuid import UUID

from app.platform.contracts import EVENT_FIELDS
from app.platform.models import AuditEvent


@dataclass(frozen=True)
class AuditInput:
    actor_id: UUID | None
    action: str
    object_type: str
    object_id: UUID | None
    details: dict
    request_id: str
    request_source: str


def record_audit(session, event: AuditInput) -> UUID:
    allowed = {"reason_code", "role_codes"} | set(EVENT_FIELDS.get(event.action, set()))
    if event.action in {
        "service.account.changed",
        "service.token.created",
        "service.token.rotated",
        "service.token.revoked",
        "service.queue.retried",
        "search.rebuild.requested",
        "document.deleted",
    }:
        allowed.add("reason")
    if set(event.details) - allowed:
        raise ValueError("Audit details are not in the approved allowlist")
    row = AuditEvent(**vars(event))
    session.add(row)
    session.flush()
    return row.id
