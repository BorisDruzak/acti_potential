import json
import logging
import signal
import threading
import time
from datetime import UTC, datetime, timedelta

from redis import Redis
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.db.session import build_engine
from app.platform.contracts import validate_payload
from app.platform.models import OutboxEvent, ProcessedEvent

CONSUMER = "platform-receipt-v1"


def process_event(session, event):
    if event.payload_version != 1:
        raise ValueError("Unsupported event payload version")
    validate_payload(event.event_type, event.payload)
    if session.get(ProcessedEvent, (CONSUMER, event.id)) is None:
        from app.content.indexing import handle_event

        handle_event(session, event)
        session.add(ProcessedEvent(consumer=CONSUMER, event_id=event.id))
        session.flush()


def process_batch(factory, limit: int) -> int:
    completed = 0
    with factory.begin() as session:
        # Serialize publication through commit. Producer timestamps are not commit order.
        session.execute(text("SELECT pg_advisory_xact_lock(731010)"))
        now = datetime.now(UTC)
        events = list(
            session.scalars(
                select(OutboxEvent)
                .where(OutboxEvent.state == "pending", OutboxEvent.next_attempt_at <= now)
                .order_by(OutboxEvent.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for event in events:
            event.attempts += 1
            try:
                with session.begin_nested():
                    process_event(session, event)
            except Exception:
                event.error_code = "handler_failed"
                event.next_attempt_at = now + timedelta(seconds=min(300, 2**event.attempts))
                if event.attempts >= 5:
                    event.state = "failed"
            else:
                if event.delivery_ordinal is None:
                    event.delivery_ordinal = session.scalar(
                        text("SELECT nextval('outbox_delivery_ordinal_seq')")
                    )
                event.processed_at = now
                event.state = "processed"
                event.error_code = None
                completed += 1
    return completed


def main():
    settings = Settings()
    engine = build_engine(settings)
    factory = sessionmaker(engine)
    redis = Redis.from_url(
        settings.redis_url.get_secret_value(), socket_timeout=1, socket_connect_timeout=1
    )
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        while not stop.is_set():
            try:
                process_batch(factory, 50)
                from app.service_api.events import cleanup_cursors

                cleanup_cursors(factory)
                redis.set("worker:heartbeat", str(time.time()), ex=30)
                from app.content.worker import process_backlog, process_extractions

                process_backlog(factory, limit=5)
                redis.set("worker:heartbeat", str(time.time()), ex=30)
                process_extractions(factory, settings.storage_root, limit=1)
                redis.set("worker:heartbeat", str(time.time()), ex=30)
            except Exception as error:
                logging.error(
                    json.dumps({"component": "worker", "error_type": type(error).__name__})
                )
            stop.wait(2)
    finally:
        redis.close()
        engine.dispose()


if __name__ == "__main__":
    main()
