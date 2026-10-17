from uuid import UUID, uuid4

from sqlalchemy import select

from app.identity.models import AuthSession
from app.registry.models import Document
from tests.test_document_deletion import create_card
from tests.test_documents import headers


def test_summaries_and_history_expose_only_named_editor_identity(client, factory):
    card = create_card(client, factory)
    me = client.get('/api/v1/users/me').json()
    expected = {'id': me['id'], 'display_name': me['display_name']}
    assert card['updated_by'] == expected
    assert client.get('/api/v1/documents').json()['items'][0]['updated_by'] == expected
    history = client.get('/api/v1/documents/' + card['id'] + '/history').json()
    assert history[0]['created_by_name'] == expected['display_name']
    with factory.begin() as db:
        original = db.get(Document, UUID(card['id']))
        old = Document(registry_id=original.registry_id, document_type_id=original.document_type_id,
                       schema_version_id=original.schema_version_id, created_by=original.created_by)
        db.add(old)
        db.flush()
        old_id = old.id
    assert client.get('/api/v1/documents/' + str(old_id)).json()['updated_by'] is None


def test_presence_is_per_tab_expires_and_never_changes_card_revision(client, factory):
    card = create_card(client, factory)
    path = '/api/v1/documents/' + card['id'] + '/collaboration'
    assert client.get(path, params={'mode': 'editor'}).json()['participants'] == []
    for editing in [True, False]:
        response = client.post(path, params={'mode': 'editor'}, json={'client_id': str(uuid4()), 'editing': editing},
                               headers=headers(client))
        assert response.status_code == 200, response.text
    state = response.json()
    me = client.get('/api/v1/users/me').json()
    assert state['participants'] == [{'id': me['id'], 'display_name': me['display_name'], 'editing': True}]
    assert state['revision'] == card['revision']
    assert state['updated_by'] == card['updated_by']
    redis = client.app.state.redis
    keys = list(redis.scan_iter(match='acti:collaboration:' + card['id'] + ':*'))
    assert len(keys) == 2
    assert all(0 < redis.ttl(key) <= 45 for key in keys)
    redis.delete(*keys)
    assert client.get(path).json()['participants'] == []


def test_latest_editor_is_revision_author_and_presence_rechecks_revoked_acl(client, factory):
    from datetime import UTC, datetime

    from app.identity.models import Role, User, UserRole
    from app.identity.sessions import issue_session
    from app.registry.models import RegistryAccess

    card = create_card(client, factory)
    original_token = client.cookies.get('acti_session')
    with factory.begin() as db:
        editor = User(display_name='Named collaborating editor')
        db.add(editor)
        db.flush()
        role = db.scalar(select(Role).where(Role.code == 'editor'))
        db.add(UserRole(user_id=editor.id, role_id=role.id))
        db.add(RegistryAccess(registry_id=UUID(card['registry_id']), user_id=editor.id,
                              permissions=['read', 'update']))
        issued = issue_session(db, editor.id, datetime.now(UTC))
        editor_id = editor.id
    client.cookies.set('acti_session', issued.token)
    path = '/api/v1/documents/' + card['id']
    update = client.put(path, json={'revision': 1, 'values': {'title': 'Edited by collaborator'}}, headers=headers(client))
    assert update.status_code == 200, update.text
    assert update.json()['updated_by'] == {'id': str(editor_id), 'display_name': 'Named collaborating editor'}
    presence = client.post(path + '/collaboration', json={'client_id': str(uuid4()), 'editing': True}, headers=headers(client))
    assert presence.status_code == 200, presence.text
    client.cookies.set('acti_session', original_token)
    state = client.get(path + '/collaboration').json()
    assert state['participants'] == [{'id': str(editor_id), 'display_name': 'Named collaborating editor', 'editing': True}]
    with factory.begin() as db:
        grant = db.get(RegistryAccess, (UUID(card['registry_id']), editor_id))
        grant.permissions = []
    assert client.get(path + '/collaboration').json()['participants'] == []


def test_presence_rechecks_session_publication_permissions_and_viewer_mode(client, factory):
    from datetime import UTC, datetime

    from app.identity.sessions import issue_session

    card = create_card(client, factory)
    path = '/api/v1/documents/' + card['id'] + '/collaboration'
    response = client.post(path, params={'mode': 'editor'}, json={'client_id': str(uuid4()), 'editing': True}, headers=headers(client))
    assert response.status_code == 200, response.text
    assert client.get(path, params={'mode': 'viewer'}).status_code == 404
    with factory.begin() as db:
        document = db.get(Document, UUID(card['id']))
        for auth in db.scalars(select(AuthSession).where(AuthSession.user_id == document.created_by)):
            auth.revoked_at = datetime.now(UTC)
        issued = issue_session(db, document.created_by, datetime.now(UTC))
    client.cookies.set('acti_session', issued.token)
    state = client.get(path)
    assert state.status_code == 200, state.text
    assert state.json()['participants'] == []
    with factory.begin() as db:
        document = db.get(Document, UUID(card['id']))
        document.card_state, document.publication_targets = 'published', ['shared']
    denied = client.post(path, params={'mode': 'viewer'}, json={'client_id': str(uuid4()), 'editing': True}, headers=headers(client))
    assert denied.status_code == 403
    assert client.post(path, params={'mode': 'viewer'}, json={'client_id': str(uuid4()), 'editing': False}, headers=headers(client)).status_code == 200
