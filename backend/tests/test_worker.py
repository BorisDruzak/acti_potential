from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from app.platform.models import OutboxEvent


def test_failed_handler_retries_and_preserves_terminal_diagnostics(factory):
    from app.jobs.worker import process_batch
    with factory.begin() as db:
        # Corrupt/external events still receive bounded retries and diagnostics;
        # the supported enqueue boundary now rejects unknown producer contracts.
        db.add(OutboxEvent(event_type="unknown.event", object_id=uuid4(), payload={}))
    for attempt in range(5):
        assert process_batch(factory, 10) == 0
        with factory.begin() as db:
            row = db.scalar(select(OutboxEvent))
            assert row.attempts == attempt + 1
            assert row.error_code == "handler_failed"
            row.next_attempt_at = datetime.now(UTC)
    with factory.begin() as db:
        assert db.scalar(select(OutboxEvent)).state == "failed"
    assert process_batch(factory, 10) == 0
