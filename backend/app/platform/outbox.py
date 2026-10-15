from uuid import UUID

from app.platform.contracts import validate_payload
from app.platform.models import OutboxEvent


def enqueue_event(session, event_type: str, object_id: UUID, payload: dict) -> UUID:
    validate_payload(event_type, payload)
    event = OutboxEvent(event_type=event_type, object_id=object_id, payload=payload)
    session.add(event)
    session.flush()
    return event.id
