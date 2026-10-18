"""A successful new parser run supersedes the displayed failure, preserving history."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from app.content.models import ContentExtraction
from app.registry.models import RegistryAccess
from tests.test_content_security import sign_in_as
from tests.test_task005_content import upload_sample


def test_current_content_hides_superseded_failure_without_erasing_it(client, factory):
    sample, _, file_id, old_id = upload_sample(client, factory)
    with factory.begin() as db:
        old = db.get(ContentExtraction, UUID(old_id))
        old.state, old.error_code = 'failed', 'limit_exceeded'
        old.created_at = datetime.now(UTC) - timedelta(days=1)
        newer = ContentExtraction(
            id=uuid4(), document_id=old.document_id, file_id=old.file_id,
            version_id=old.version_id, sha256=old.sha256,
            fingerprint=uuid4().hex, parser=old.parser, parser_version='new-parser-version',
            state='completed', attempts=1, full_text='Complete text',
        )
        db.add(newer)
        db.add(RegistryAccess(registry_id=sample['registry'], user_id=sample['admin'].id,
                              permissions=['read', 'download']))
        newer_id = str(newer.id)
    sign_in_as(client, factory, sample['viewer'])
    response = client.get(f"/api/v1/documents/{sample['document']}/content")
    assert response.status_code == 200, response.text
    assert response.json()['total'] == 1
    assert [(item['id'], item['state']) for item in response.json()['items']] == [(newer_id, 'completed')]
    sign_in_as(client, factory, sample['admin'])
    errors = client.get('/api/v1/admin/content/errors')
    assert errors.status_code == 200, errors.text
    assert errors.json()['total'] == 0
    with factory() as db:
        history = list(db.scalars(select(ContentExtraction).where(ContentExtraction.file_id == UUID(file_id))))
        assert len(history) == 2
        assert db.get(ContentExtraction, UUID(old_id)).error_code == 'limit_exceeded'
