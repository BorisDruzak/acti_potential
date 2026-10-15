from uuid import UUID, uuid4

from sqlalchemy import select

from app.jobs.worker import process_batch
from app.platform.models import OutboxEvent
from tests.test_task010_service_acl import service_get, service_sample


def find_event(client, bearer, identity, cursor=None):
    for _ in range(20):
        page = service_get(
            client, bearer, "/events?page_size=100" + (f"&cursor={cursor}" if cursor else "")
        ).json()
        cursor = page["next_cursor"]
        if any(event["id"] == str(identity) for event in page["items"]):
            return cursor
    raise AssertionError("Committed published event was lost after the consumer bookmark")


def test_late_producer_commit_is_delivered_after_newer_committed_event(client, factory):
    setup, _, bearer = service_sample(client, factory)
    process_batch(factory, 1000)
    delayed = factory()
    try:
        late = OutboxEvent(
            event_type="document.metadata.updated",
            object_id=UUID(setup["source"]["id"]),
            payload={"revision": 1},
        )
        delayed.add(late)
        delayed.flush()  # Earlier created_at, deliberately still invisible to another transaction.
        with factory.begin() as db:
            early = OutboxEvent(
                event_type="document.metadata.updated",
                object_id=UUID(setup["source"]["id"]),
                payload={"revision": 2},
            )
            db.add(early)
            db.flush()
            early_id = early.id
        assert process_batch(factory, 1000) == 1
        cursor = find_event(client, bearer, early_id)
        late_id = late.id
        delayed.commit()
        assert process_batch(factory, 1000) == 1
        find_event(client, bearer, late_id, cursor)
        with factory() as db:
            first = db.get(OutboxEvent, early_id)
            second = db.get(OutboxEvent, late_id)
            assert second.created_at < first.created_at
            assert second.delivery_ordinal > first.delivery_ordinal
    finally:
        delayed.close()


def test_published_event_replay_keeps_delivery_order_and_single_receipt(client, factory):
    from app.jobs.worker import CONSUMER
    from app.platform.models import ProcessedEvent

    service_sample(client, factory)
    process_batch(factory, 1000)
    with factory.begin() as db:
        event = db.scalar(select(OutboxEvent).where(OutboxEvent.state == "processed"))
        identity, ordinal = event.id, event.delivery_ordinal
        assert ordinal is not None
        event.state = "pending"
    process_batch(factory, 1000)
    with factory() as db:
        assert db.get(OutboxEvent, identity).delivery_ordinal == ordinal
        assert db.get(ProcessedEvent, (CONSUMER, identity)) is not None


def test_existing_outbox_backfill_preserves_events_and_publishes_pending_after_watermark(factory):
    from pathlib import Path

    import pytest
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.exc import DBAPIError

    from app.db.migrate import upgrade_database

    with factory.begin() as db:
        published = OutboxEvent(event_type="form.schema.published", object_id=uuid4(), payload={})
        db.add(published)
        db.flush()
        published_id = published.id
    assert process_batch(factory, 10) == 1
    with factory.begin() as db:
        pending = OutboxEvent(event_type="form.schema.published", object_id=uuid4(), payload={})
        failed = OutboxEvent(
            event_type="form.schema.published", object_id=uuid4(), payload={}, state="failed"
        )
        db.add_all([pending, failed])
        db.flush()
        pending_id, failed_id = pending.id, failed.id
    engine = factory.kw["bind"]
    with engine.begin() as connection:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        command.downgrade(config, "0009_execution")
    upgrade_database(engine)
    with factory() as db:
        assert set(db.scalars(select(OutboxEvent.id))) == {published_id, pending_id, failed_id}
        watermark = db.get(OutboxEvent, published_id).delivery_ordinal
        assert watermark is not None
        assert db.get(OutboxEvent, pending_id).delivery_ordinal is None
        assert db.get(OutboxEvent, failed_id).delivery_ordinal is None
    assert process_batch(factory, 10) == 1
    with factory() as db:
        assert db.get(OutboxEvent, pending_id).delivery_ordinal > watermark
    with pytest.raises(DBAPIError), factory.begin() as db:
        db.get(OutboxEvent, published_id).delivery_ordinal = watermark + 1000
