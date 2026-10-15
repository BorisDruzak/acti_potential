from dataclasses import dataclass
from uuid import UUID

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
    if set(event.details) - {"reason_code", "role_codes"}:
        raise ValueError("Audit details are not in the approved allowlist")
    row = AuditEvent(**vars(event))
    session.add(row)
    session.flush()
    return row.id
