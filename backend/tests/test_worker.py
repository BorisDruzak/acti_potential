from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from app.platform.models import OutboxEvent


def test_failed_handler_retries_and_preserves_terminal_diagnostics(factory):
    from app.jobs.worker import process_batch
    from app.platform.outbox import enqueue_event

    with factory.begin() as db:
        enqueue_event(db, "unknown.event", uuid4(), {})
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
