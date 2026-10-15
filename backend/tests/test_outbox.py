from sqlalchemy import func, select

from app.platform.models import OutboxEvent, ProcessedEvent


def test_outbox_not_visible_before_commit_and_replay_is_idempotent(factory):
    from app.identity.bootstrap import bootstrap_admin
    from app.jobs.worker import process_batch

    with factory.begin() as db:
        bootstrap_admin(db, "admin", "Admin", "synthetic-password-for-tests")
        assert process_batch(factory, 10) == 0
    assert process_batch(factory, 10) == 1
    with factory.begin() as db:
        event = db.scalar(select(OutboxEvent))
        assert event.state == "processed"
        event.state = "pending"  # Replay after lost acknowledgement.
    assert process_batch(factory, 10) == 1
    with factory.begin() as db:
        assert db.scalar(select(func.count()).select_from(ProcessedEvent)) == 1
