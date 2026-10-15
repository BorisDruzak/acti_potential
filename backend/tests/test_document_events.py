from uuid import uuid4

import pytest


def test_document_outbox_approved_contract_and_receipt(factory):
    from app.jobs.worker import process_batch
    from app.platform.outbox import enqueue_event

    with factory.begin() as db:
        enqueue_event(db, "document.created", uuid4(), {"revision": 1})
    assert process_batch(factory, 10) == 1


def test_document_outbox_rejects_content_and_unknown_event(factory):
    from app.platform.outbox import enqueue_event

    with factory.begin() as db:
        with pytest.raises(ValueError):
            enqueue_event(db, "document.created", uuid4(), {"title": "private"})
        with pytest.raises(ValueError):
            enqueue_event(db, "invented.action", uuid4(), {})
