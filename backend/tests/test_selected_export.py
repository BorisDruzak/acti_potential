import csv
import io
from uuid import uuid4

from tests.test_documents import headers, setup_registry


def cards(client, factory):
    registry = setup_registry(client, factory)
    result = []
    for title in ['Selected first', 'Unselected second', 'Selected third']:
        response = client.post('/api/v1/documents', json={
            'registry_id': registry['id'], 'document_type_id': registry['document_type_ids'][0],
            'values': {'title': title},
        }, headers=headers(client))
        assert response.status_code == 201, response.text
        result.append(response.json())
    return result


def test_selection_export_is_exact_and_keeps_requested_order(client, factory):
    first, second, third = cards(client, factory)
    response = client.post('/api/v1/documents/export-selection', json={
        'document_ids': [third['id'], first['id']], 'format': 'csv',
        'columns': ['title'], 'mode': 'editor',
    }, headers=headers(client))
    assert response.status_code == 200, response.text
    rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
    assert rows == [['Наименование документа [title]'], ['Selected third'], ['Selected first']]
    assert second['title'] not in response.text


def test_selection_export_never_silently_skips_an_unavailable_id(client, factory):
    first, _, _ = cards(client, factory)
    response = client.post('/api/v1/documents/export-selection', json={
        'document_ids': [first['id'], str(uuid4())], 'format': 'xlsx', 'mode': 'editor',
    }, headers=headers(client))
    assert response.status_code == 404, response.text
    assert response.headers['content-type'].startswith('application/json')


def test_selection_export_rejects_duplicates_empty_and_missing_csrf(client, factory):
    first, _, _ = cards(client, factory)
    for ids in [[], [first['id'], first['id']]]:
        response = client.post('/api/v1/documents/export-selection', json={
            'document_ids': ids, 'format': 'csv',
        }, headers=headers(client))
        assert response.status_code == 422, response.text
    assert client.post('/api/v1/documents/export-selection', json={
        'document_ids': [first['id']], 'format': 'csv',
    }, headers={'Origin': 'https://testserver'}).status_code == 403
