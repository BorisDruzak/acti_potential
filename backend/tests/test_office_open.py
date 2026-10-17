from urllib.parse import urlsplit
from uuid import UUID

from tests.test_documents import headers, setup_registry


def original(client, factory):
    registry = setup_registry(client, factory)
    card = client.post('/api/v1/documents', json={
        'registry_id': registry['id'], 'document_type_id': registry['document_type_ids'][0],
        'values': {'title': 'Synthetic office original'},
    }, headers=headers(client)).json()
    response = client.post('/api/v1/documents/' + card['id'] + '/files',
        params={'filename': 'original.docx', 'revision': card['revision']},
        content=b'owned immutable office bytes',
        headers={**headers(client), 'Content-Type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'})
    assert response.status_code == 201, response.text
    return response.json()


def ticket_url(client, file_id, application='word'):
    response = client.post('/api/v1/files/' + file_id + '/office-open',
        json={'application': application, 'mode': 'editor'}, headers=headers(client))
    assert response.status_code == 200, response.text
    prefix = 'ms-word:ofv|u|' if application == 'word' else 'vnd.libreoffice.command:ofv|u|'
    assert response.json()['uri'].startswith(prefix + 'https://testserver/')
    assert client.cookies.get('acti_session') not in response.json()['uri']
    url = response.json()['uri'].removeprefix(prefix)
    return urlsplit(url).path + '?' + urlsplit(url).query


def test_office_opens_exact_original_without_browser_cookie_and_supports_head(client, factory):
    card = original(client, factory)
    for application in ['word', 'libreoffice']:
        url = ticket_url(client, card['files'][0]['id'], application)
        saved = dict(client.cookies)
        client.cookies.clear()
        response = client.head(url)
        assert response.status_code == 200, response.text
        assert response.headers['content-length'] == str(len(b'owned immutable office bytes'))
        response = client.get(url)
        assert response.status_code == 200, response.text
        assert response.content == b'owned immutable office bytes'
        assert response.headers['referrer-policy'] == 'no-referrer'
        client.cookies.update(saved)


def test_office_ticket_rechecks_acl_and_session_revocation(client, factory):
    from app.registry.models import RegistryAccess

    card = original(client, factory)
    url = ticket_url(client, card['files'][0]['id'])
    with factory.begin() as db:
        access = db.get(RegistryAccess, (UUID(card['registry_id']), UUID(card['files'][0]['created_by'])))
        access.permissions = ['read', 'update']
    assert client.get(url).status_code == 403
    with factory.begin() as db:
        db.get(RegistryAccess, (UUID(card['registry_id']), UUID(card['files'][0]['created_by']))).permissions = ['read', 'update', 'download']
    assert client.post('/api/v1/auth/logout', headers=headers(client)).status_code == 204
    assert client.get(url).status_code == 401


def test_office_ticket_expiry_and_input_rejection(client, factory):
    card = original(client, factory)
    url = ticket_url(client, card['files'][0]['id'])
    keys = list(client.app.state.redis.scan_iter('office:open:*'))
    assert len(keys) == 1
    assert 0 < client.app.state.redis.ttl(keys[0]) <= 120
    client.app.state.redis.delete(keys[0])
    assert client.get(url).status_code == 404
    assert client.post('/api/v1/files/' + card['files'][0]['id'] + '/office-open',
        json={'application': 'shell'}, headers=headers(client)).status_code == 422


def test_office_capability_cannot_serve_uploaded_active_content_on_app_origin(client, factory):
    card = original(client, factory)
    response = client.post('/api/v1/documents/' + card['id'] + '/files',
        params={'filename': 'untrusted.docx', 'revision': card['revision']},
        content=b'<html><script>window.active_content=true</script></html>',
        headers={**headers(client), 'Content-Type': 'text/html'})
    assert response.status_code == 201, response.text
    url = ticket_url(client, response.json()['files'][-1]['id'])
    result = client.get(url)
    assert result.status_code == 200
    assert result.headers['content-disposition'].startswith('attachment;')
    assert 'text/html' not in result.headers['content-type']
    assert "default-src 'none'" in result.headers['content-security-policy']
    assert 'sandbox' in result.headers['content-security-policy']
