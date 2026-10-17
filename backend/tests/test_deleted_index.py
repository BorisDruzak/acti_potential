from uuid import UUID

from sqlalchemy import select

from app.content.indexing import index_metadata
from app.content.models import MetadataFragment
from app.content.worker import process_backlog
from app.jobs.worker import process_batch
from tests.test_document_deletion import create_card
from tests.test_documents import headers


def test_deleted_card_is_not_reindexed_or_queued_as_recovery_backlog(client, factory):
    card = create_card(client, factory)
    with factory.begin() as db:
        index_metadata(db, UUID(card['id']))
        assert list(db.scalars(select(MetadataFragment).where(MetadataFragment.document_id == UUID(card['id']))))
    response = client.request('DELETE', '/api/v1/documents/' + card['id'],
        json={'revision': card['revision'], 'reason': 'Owned fixture removed'}, headers=headers(client))
    assert response.status_code == 204, response.text
    process_batch(factory, 100)
    assert process_backlog(factory) == 0
    with factory() as db:
        assert not list(db.scalars(select(MetadataFragment).where(MetadataFragment.document_id == UUID(card['id']))))
