from uuid import UUID

import pytest
from sqlalchemy import select

from app.registry.models import Document, FormSchema, Registry
from tests.test_documents import headers, setup_registry


def archive(factory, actor_id, kind, identity):
    from app.registry.models import ConfigurationArchive

    with factory.begin() as db:
        db.add(ConfigurationArchive(object_type=kind, object_id=UUID(identity),
                                    archived_by=UUID(actor_id), reason='Owned fixture cleanup'))


def test_archived_registry_hides_cards_scoped_references_and_direct_mutations(client, factory):
    registry = setup_registry(client, factory)
    me = client.get('/api/v1/users/me').json()
    reference = client.post('/api/v1/references', json={
        'code': 'archive-scoped', 'name': 'Synthetic archive reference', 'registry_id': registry['id'],
    }, headers=headers(client)).json()
    created = client.post('/api/v1/documents', json={
        'registry_id': registry['id'], 'document_type_id': registry['document_type_ids'][0],
        'values': {'title': 'Retained immutable archive card'},
    }, headers=headers(client))
    assert created.status_code == 201, created.text
    card = created.json()
    assert client.get('/api/v1/documents/' + card['id']).status_code == 200
    assert client.get('/api/v1/references/' + reference['id'] + '/items').status_code == 200
    archive(factory, me['id'], 'registry', registry['id'])
    assert registry['id'] not in {r['id'] for r in client.get('/api/v1/registries').json()}
    assert reference['id'] not in {r['id'] for r in client.get('/api/v1/references').json()}
    assert client.get('/api/v1/documents', params={'registry_id': registry['id']}).json()['total'] == 0
    for suffix in ['', '/history', '/collaboration', '/acl']:
        assert client.get('/api/v1/documents/' + card['id'] + suffix).status_code == 404
    assert client.get('/api/v1/references/' + reference['id'] + '/items').status_code == 404
    assert client.patch('/api/v1/registries/' + registry['id'], json={'name': 'Cannot change archived'},
                        headers=headers(client)).status_code == 404
    assert client.post('/api/v1/documents', json={
        'registry_id': registry['id'], 'document_type_id': registry['document_type_ids'][0],
        'values': {'title': 'Cannot add to archived registry'},
    }, headers=headers(client)).status_code in {403, 404}
    with factory() as db:
        document = db.get(Document, UUID(card['id']))
        assert document.deleted_at is None and document.revision == 1
        assert db.get(Registry, UUID(registry['id'])) is not None
        assert db.get(FormSchema, document.schema_version_id).state == 'published'


def test_archived_type_is_hidden_from_choices_schemas_and_assignment(client, factory):
    registry = setup_registry(client, factory)
    me = client.get('/api/v1/users/me').json()
    kind = client.post('/api/v1/document-types', json={'code': 'archive-kind', 'name': 'Synthetic archived type'},
                       headers=headers(client)).json()
    version = client.get('/api/v1/document-types/' + kind['id'] + '/versions').json()[0]
    assert client.post('/api/v1/form-schemas/' + version['id'] + '/publish', json={}, headers=headers(client)).status_code == 200
    assert client.patch('/api/v1/registries/' + registry['id'],
                        json={'document_type_ids': [kind['id']]}, headers=headers(client)).status_code == 200
    archive(factory, me['id'], 'document_type', kind['id'])
    assert kind['id'] not in {r['id'] for r in client.get('/api/v1/document-types').json()}
    assert client.get('/api/v1/document-types/' + kind['id'] + '/versions').status_code == 404
    assert client.get('/api/v1/form-schemas/' + version['id']).status_code == 404
    assert client.put('/api/v1/form-schemas/' + version['id'], json={'schema': {'sections': []}},
                      headers=headers(client)).status_code == 404
    assert client.patch('/api/v1/registries/' + registry['id'],
                        json={'document_type_ids': [kind['id']]}, headers=headers(client)).status_code == 422
    assert client.post('/api/v1/documents', json={
        'registry_id': registry['id'], 'document_type_id': kind['id'], 'values': {},
    }, headers=headers(client)).status_code in {404, 422}
    with factory() as db:
        assert db.get(FormSchema, UUID(version['id'])) is not None


def test_reference_archive_is_reversible_without_rewriting_items_or_history(client, factory):
    from app.registry.models import ReferenceItem, ReferenceSet

    setup_registry(client, factory)
    me = client.get('/api/v1/users/me').json()
    ref = client.post('/api/v1/references', json={'code': 'archive-reference', 'name': 'Synthetic retained reference'},
                      headers=headers(client)).json()
    item = client.post('/api/v1/references/' + ref['id'] + '/items',
                       json={'label': 'Retained classification'}, headers=headers(client)).json()
    archive(factory, me['id'], 'reference_set', ref['id'])
    assert ref['id'] not in {r['id'] for r in client.get('/api/v1/references').json()}
    for suffix in ['/items', '/history', '/export']:
        assert client.get('/api/v1/references/' + ref['id'] + suffix).status_code == 404
    assert client.patch('/api/v1/references/' + ref['id'], json={'name': 'Cannot change archived'},
                        headers=headers(client)).status_code == 404
    with factory.begin() as db:
        from app.registry.models import ConfigurationArchive

        assert db.get(ReferenceItem, UUID(item['id'])).active
        assert db.get(ReferenceSet, UUID(ref['id'])) is not None
        marker = db.scalar(select(ConfigurationArchive).where(ConfigurationArchive.object_id == UUID(ref['id'])))
        db.delete(marker)
    assert ref['id'] in {r['id'] for r in client.get('/api/v1/references').json()}
    assert client.get('/api/v1/references/' + ref['id'] + '/items').json()[0]['id'] == item['id']


def test_archived_reference_rejects_guessed_values_but_preserves_existing_card(client, factory):
    registry = setup_registry(client, factory)
    me = client.get('/api/v1/users/me').json()
    candidates = [r for r in client.get('/api/v1/references').json()
                  if r['code'] == 'themes' and r['registry_id'] in {registry['id'], None}]
    reference = next((r for r in candidates if r['registry_id'] == registry['id']), candidates[0])
    item = client.post('/api/v1/references/' + reference['id'] + '/items',
                       json={'label': 'Synthetic retained theme'}, headers=headers(client)).json()
    created = client.post('/api/v1/documents', json={
        'registry_id': registry['id'], 'document_type_id': registry['document_type_ids'][0],
        'values': {'title': 'Existing theme card', 'theme_id': item['id']},
    }, headers=headers(client))
    assert created.status_code == 201, created.text
    card = created.json()
    archive(factory, me['id'], 'reference_set', reference['id'])
    assert client.put('/api/v1/documents/' + card['id'], json={
        'revision': card['revision'], 'values': {'theme_id': item['id']},
    }, headers=headers(client)).status_code == 422
    retained = client.put('/api/v1/documents/' + card['id'], json={
        'revision': card['revision'], 'values': {'title': 'Unrelated edit keeps saved theme'},
    }, headers=headers(client))
    assert retained.status_code == 200, retained.text
    assert retained.json()['values']['theme_id'] == item['id']


def test_archived_registry_is_absent_from_process_knowledge_execution_search_and_direct_reads(client, factory):
    from tests.test_task008_knowledge_acl import knowledge_sample, publish_case, publish_template

    setup, security = knowledge_sample(client, factory)
    template, _ = publish_template(client, security, setup)
    case = publish_case(client, security, setup)
    started = client.post('/api/v1/execution/instances', json={
        'process_version_id': setup['version']['id'], 'name': 'Synthetic archived execution',
    }, headers=security)
    assert started.status_code == 201, started.text
    instance = started.json()
    registry_id = str(setup['process_registry'])
    list_paths = {
        '/api/v1/processes': 'list', '/api/v1/processes/catalog': 'page',
        '/api/v1/knowledge/templates': 'page', '/api/v1/knowledge/cases': 'page',
        '/api/v1/execution/instances': 'page',
    }
    for path, shape in list_paths.items():
        response = client.get(path, params={'registry_id': registry_id})
        assert response.status_code == 200, response.text
        assert len(response.json()) if shape == 'list' else response.json()['total']
    me = client.get('/api/v1/users/me').json()
    archive(factory, me['id'], 'registry', registry_id)
    observed = {}
    for path, shape in list_paths.items():
        response = client.get(path, params={'registry_id': registry_id})
        observed[path] = (response.status_code, len(response.json()) if shape == 'list' else response.json().get('total'))
    for path in [
        '/api/v1/processes/' + setup['process']['id'],
        '/api/v1/process-versions/' + setup['version']['id'],
        '/api/v1/knowledge/templates/' + template['id'],
        '/api/v1/knowledge/cases/' + case['id'],
        '/api/v1/execution/instances/' + instance['id'],
    ]:
        observed[path] = client.get(path).status_code
    assert observed == {**{path: (200, 0) for path in list_paths},
                        **{path: 404 for path in observed if path not in list_paths}}


@pytest.mark.parametrize('kind', ['registry', 'number_series'])
def test_archived_number_books_are_hidden_even_from_administrators_but_entries_retained(client, factory, kind):
    from app.numbering.models import NumberEntry
    from tests.test_task004_numbers import setup_series

    series, registry, _, me, security = setup_series(client, factory, synthetic=True)
    reserved = client.post('/api/v1/number-series/' + series['id'] + '/reserve',
                           json={'count': 1, 'idempotency_key': 'archive-retained-number'}, headers=security)
    assert reserved.status_code == 200, reserved.text
    assert series['id'] in {row['id'] for row in client.get('/api/v1/number-series').json()}
    archive(factory, me['id'], kind, registry['id'] if kind == 'registry' else series['id'])
    assert series['id'] not in {row['id'] for row in client.get('/api/v1/number-series').json()}
    assert client.get('/api/v1/number-series/' + series['id'] + '/grants').status_code == 404
    assert client.post('/api/v1/number-series/' + series['id'] + '/reserve',
                       json={'count': 1, 'idempotency_key': 'cannot-reserve-archived'}, headers=security).status_code == 404
    if kind == 'number_series':
        assert registry['id'] in {row['id'] for row in client.get('/api/v1/registries').json()}
    with factory() as db:
        rows = list(db.scalars(select(NumberEntry).where(NumberEntry.series_id == UUID(series['id']))))
        assert len(rows) == 1 and rows[0].state == 'reserved'


@pytest.mark.parametrize('kind', ['registry', 'document_type'])
def test_number_series_scope_rejects_archived_registry_or_document_type(client, factory, kind):
    from tests.test_task004_numbers import setup_series

    _, registry, dtype, me, security = setup_series(client, factory, synthetic=True)
    archive(factory, me['id'], kind, registry['id'] if kind == 'registry' else dtype['id'])
    created = client.post('/api/v1/number-series', json={
        'code': 'cannot-use-archived', 'name': 'Unavailable scope',
        'registry_id': registry['id'], 'document_type_ids': [dtype['id']], 'synthetic': True,
    }, headers=security)
    assert created.status_code == 422, created.text
