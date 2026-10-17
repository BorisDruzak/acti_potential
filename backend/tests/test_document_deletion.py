from uuid import UUID

from sqlalchemy import select

from app.platform.models import AuditEvent
from app.registry.models import Document, DocumentFile, MetadataRevision
from tests.test_documents import headers, setup_registry


def create_card(client, factory):
    registry = setup_registry(client, factory)
    response = client.post('/api/v1/documents', json={
        'registry_id': registry['id'], 'document_type_id': registry['document_type_ids'][0],
        'values': {'title': 'Synthetic retained card', 'reg_number': 'KEEP-001'},
    }, headers=headers(client))
    assert response.status_code == 201, response.text
    return response.json()


def test_delete_is_revision_checked_and_retains_original_history_and_number(client, factory):
    card = create_card(client, factory)
    upload = client.post('/api/v1/documents/' + card['id'] + '/files',
                         params={'filename': 'retained.txt', 'revision': card['revision']},
                         content=b'immutable original', headers={**headers(client), 'Content-Type': 'text/plain'})
    assert upload.status_code == 201, upload.text
    current = upload.json()
    path = '/api/v1/documents/' + card['id']
    stale = client.request('DELETE', path, json={'revision': card['revision'], 'reason': 'Duplicate'},
                           headers=headers(client))
    assert stale.status_code == 409, stale.text
    response = client.request('DELETE', path, json={'revision': current['revision'], 'reason': ' Duplicate '},
                              headers=headers(client))
    assert response.status_code == 204, response.text
    for suffix in ['', '/history', '/acl', '/collaboration']:
        assert client.get(path + suffix).status_code == 404
    assert client.get('/api/v1/documents', params={'q': 'Synthetic retained card'}).json()['total'] == 0
    assert client.get('/api/v1/files/' + current['files'][0]['id'] + '/download').status_code == 404
    assert client.put(path, json={'revision': current['revision'], 'values': {'title': 'Revive'}},
                      headers=headers(client)).status_code == 404
    with factory() as db:
        document = db.get(Document, UUID(card['id']))
        assert document.deleted_at is not None
        assert document.deleted_reason == 'Duplicate'
        assert document.reg_number == 'KEEP-001'
        assert document.revision == current['revision'] + 1
        assert document.deleted_by == document.created_by
        assert db.scalar(select(DocumentFile).where(DocumentFile.document_id == document.id)) is not None
        assert len(list(db.scalars(select(MetadataRevision).where(MetadataRevision.document_id == document.id)))) == 3
        audit = db.scalar(select(AuditEvent).where(AuditEvent.action == 'document.deleted', AuditEvent.object_id == document.id))
        assert audit.details == {'revision': current['revision'] + 1, 'reason': 'Duplicate'}


def test_delete_requires_update_and_a_nonblank_reason(client, factory):
    from app.registry.models import RegistryAccess

    card = create_card(client, factory)
    path = '/api/v1/documents/' + card['id']
    assert client.request('DELETE', path, json={'revision': 1, 'reason': '   '}, headers=headers(client)).status_code == 422
    with factory.begin() as db:
        document = db.get(Document, UUID(card['id']))
        access = db.get(RegistryAccess, (document.registry_id, document.created_by))
        access.permissions = ['read', 'download']
    assert client.request('DELETE', path, json={'revision': 1, 'reason': 'Duplicate'}, headers=headers(client)).status_code == 403
