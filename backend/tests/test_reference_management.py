from uuid import UUID

from sqlalchemy import select

from app.registry.models import ReferenceItem, ReferenceSet, Registry
from tests.test_content_security import actors_and_document, sign_in_as


def test_service_retrieval_scopes_do_not_grant_reference_administration():
    from uuid import uuid4

    from app.identity.policy import Actor
    from app.registry.reference_management import can_manage_reference

    actor = Actor(uuid4(), 'Scoped actor', frozenset({'admin'}),
                  scoped_operations=frozenset({'metadata'}))
    assert can_manage_reference(None, actor, None) is False


def test_editor_manages_own_registry_references_without_erasing_hidden_attributes(client, factory):
    sample = actors_and_document(factory)
    with factory.begin() as db:
        book = ReferenceSet(code='signers', name='Подписанты', registry_id=sample['registry'])
        db.add(book)
        db.flush()
        item = ReferenceItem(reference_set_id=book.id, label='Сотрудник', code='stable', sort_order=8,
                             item_metadata={'position': 'Советник', 'private': 'keep'},
                             source_database='okrug', source_entity='signers', source_id='1')
        db.add(item)
        db.flush()
        reference_id, item_id = str(book.id), str(item.id)
    security = sign_in_as(client, factory, sample['editor'])
    result = client.get('/api/v1/references', params={'manage': True})
    assert result.status_code == 200, result.text
    assert next(row for row in result.json() if row['id'] == reference_id)['editable'] is True
    changed = client.patch(f'/api/v1/references/{reference_id}/items/{item_id}', json={
        'label': 'Новое имя', 'metadata': {'position': 'Юрист'}, 'description': 'Описание',
    }, headers=security)
    assert changed.status_code == 200, changed.text
    assert 'private' not in changed.json()['metadata'] and changed.json()['source_id'] is None
    with factory() as db:
        row = db.get(ReferenceItem, UUID(item_id))
        assert row.code == 'stable' and row.sort_order == 8 and row.source_id == '1'
        assert row.item_metadata == {'position': 'Юрист', 'private': 'keep'}
    assert client.get(f'/api/v1/references/{reference_id}/history').status_code == 200
    created = client.post('/api/v1/references', json={
        'name': 'Новый справочник', 'registry_id': str(sample['registry']),
        'initial_items': [{'label': 'Первое'}, {'label': 'Второе', 'active': False}],
    }, headers=security)
    assert created.status_code == 201, created.text
    items = client.get(f"/api/v1/references/{created.json()['id']}/items")
    assert [row['label'] for row in items.json()] == ['Второе', 'Первое']


def test_reference_management_denies_readers_foreign_and_global_changes(client, factory):
    sample = actors_and_document(factory)
    with factory.begin() as db:
        foreign = Registry(code='foreign', name='Другой реестр')
        db.add(foreign)
        db.flush()
        books = [ReferenceSet(code='own', name='Свой', registry_id=sample['registry']),
                 ReferenceSet(code='global', name='Общий'),
                 ReferenceSet(code='foreign', name='Чужой', registry_id=foreign.id)]
        db.add_all(books)
        db.flush()
        ids = [str(book.id) for book in books]
    security = sign_in_as(client, factory, sample['editor'])
    for reference_id in ids[1:]:
        assert client.post(f'/api/v1/references/{reference_id}/items', json={'label': 'Запрещено'}, headers=security).status_code in {403, 404}
    assert client.patch(f'/api/v1/references/{ids[0]}', json={'code': 'replace'}, headers=security).status_code == 403
    assert client.post('/api/v1/references', json={'name': 'Общий'}, headers=security).status_code == 403
    assert client.post('/api/v1/references', json={
        'name': 'Подмена', 'code': 'global', 'registry_id': str(sample['registry']),
    }, headers=security).status_code == 403
    security = sign_in_as(client, factory, sample['viewer'])
    assert client.get('/api/v1/references?manage=true').status_code == 403
    assert client.post(f'/api/v1/references/{ids[0]}/items', json={'label': 'Запрещено'}, headers=security).status_code == 403
    with factory() as db:
        assert list(db.scalars(select(ReferenceItem))) == []


def test_reference_creation_rolls_back_the_whole_draft_if_values_conflict(client, factory):
    sample = actors_and_document(factory)
    security = sign_in_as(client, factory, sample['editor'])
    response = client.post('/api/v1/references', json={
        'name': 'Не должно сохраниться', 'registry_id': str(sample['registry']),
        'initial_items': [{'label': 'Одинаковое'}, {'label': 'Одинаковое'}],
    }, headers=security)
    assert response.status_code == 409, response.text
    with factory() as db:
        assert db.scalar(select(ReferenceSet).where(ReferenceSet.name == 'Не должно сохраниться')) is None
