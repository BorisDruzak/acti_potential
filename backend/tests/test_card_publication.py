from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.content.api import authorize_file
from app.content.contracts import SearchInput
from app.identity.policy import Actor
from app.registry.document_contracts import DocumentCreate, DocumentUpdate
from app.registry.document_search import SearchParameters, search_query
from app.registry.documents import get_document
from app.registry.models import Document, DocumentACL
from app.registry.policy import accessible_document_clause, role_actions
from tests.test_content_security import actors_and_document


def publication_cards(factory, fields=None):
    ids = actors_and_document(factory, fields=fields)
    with factory.begin() as db:
        original = db.get(Document, ids['document'])
        original.card_state, original.publication_targets = 'published', ['internal']
        for name, state, targets in [
            ('draft', 'draft', ['shared']),
            ('shared', 'published', ['internal', 'shared']),
        ]:
            item = Document(registry_id=original.registry_id,
                            document_type_id=original.document_type_id,
                            schema_version_id=original.schema_version_id,
                            created_by=original.created_by, title=name,
                            card_state=state, publication_targets=targets)
            db.add(item)
            db.flush()
            ids[name] = item.id
    return ids


def test_lifecycle_input_defaults_are_private_and_distinct_from_legal_status():
    from uuid import uuid4

    value = DocumentCreate(registry_id=uuid4(), document_type_id=uuid4(), values={})
    assert value.card_state == 'draft'
    assert value.publication_targets == ['internal']
    assert DocumentUpdate(revision=1, values={}).card_state is None
    with pytest.raises(ValueError):
        DocumentCreate(registry_id=uuid4(), document_type_id=uuid4(), values={},
                       publication_targets=['shared', 'shared'])
    assert 'create' not in role_actions(Actor(uuid4(), 'Reader user', frozenset({'user'})))


def test_reader_scope_is_sql_filtered_and_cannot_be_widened_by_editor_mode(factory):
    ids = publication_cards(factory)
    with factory() as db:
        for role in ['viewer', 'user']:
            actor = Actor(ids['viewer'].id, 'Reader', frozenset({role}))
            visible = list(db.scalars(select(Document.id).where(accessible_document_clause(db, actor))))
            assert visible == [ids['shared']]
            for hidden in [ids['document'], ids['draft']]:
                with pytest.raises(HTTPException) as denied:
                    get_document(db, actor, hidden, mode='editor')
                assert denied.value.status_code == 404
                with pytest.raises(HTTPException):
                    authorize_file(db, actor, SimpleNamespace(document_id=hidden), mode='editor')
        editor = ids['editor']
        assert len(list(db.scalars(select(Document.id).where(accessible_document_clause(db, editor))))) == 3
        assert list(db.scalars(select(Document.id).where(accessible_document_clause(db, editor, 'viewer')))) == [ids['shared']]


def test_card_state_filter_and_acl_do_not_leak_draft_counts(factory):
    ids = publication_cards(factory)
    with factory() as db:
        for actor, expected in [(ids['editor'], 1), (ids['viewer'], 0)]:
            query, _ = search_query(db, actor, SearchParameters(card_state='draft', mode='editor'))
            assert len(list(db.scalars(query))) == expected
        assert SearchInput(card_state='published').legacy_filters().card_state == 'published'
    with factory.begin() as db:
        db.add(DocumentACL(document_id=ids['shared'], user_id=ids['editor'].id, permissions=['read']))
    with factory() as db:
        assert list(db.scalars(select(Document.id).where(accessible_document_clause(db, ids['viewer'])))) == []


def test_legacy_target_defaults_are_official_but_not_shared():
    from migration.defaults import legacy_card_defaults

    for database in ['okrug', 'raion']:
        assert legacy_card_defaults(database) == {'card_state': 'published', 'publication_targets': ['internal']}
    with pytest.raises(ValueError):
        legacy_card_defaults('unapproved-source')


def test_viewer_preview_hides_private_relations_reference_labels_and_metadata(factory):
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.content.chunks import read_chunk
    from app.content.models import MetadataFragment
    from app.content.serialization import summaries
    from app.execution.service import result_output
    from app.knowledge.service import related
    from app.processes.service import basis
    from app.registry.document_export import display_values
    from app.registry.models import FormSchema
    from app.registry.policy import document_permissions
    from app.relations.api import graph, options
    from app.relations.service import endpoints, visible_relations

    ids = publication_cards(factory, fields=[
        {'key': 'title', 'label': 'Title', 'type': 'text', 'system': 'title'},
        {'key': 'public', 'label': 'Public', 'type': 'text'},
        {'key': 'linked', 'label': 'Linked', 'type': 'document'},
    ])
    with factory.begin() as db:
        private = db.get(Document, ids['document'])
        fragment = MetadataFragment(document_id=private.id, document_revision=private.revision,
                                    field_key='public', label='Public', text='Private metadata')
        db.add(fragment)
        shared = db.get(Document, ids['shared'])
        shared.custom_data = {'linked': str(private.id)}
        db.flush()
        fragment_id = fragment.id
    with factory() as db:
        editor = ids['editor']
        private = db.get(Document, ids['document'])
        shared = db.get(Document, ids['shared'])
        assert document_permissions(db, ids['viewer'], private) == set()
        assert read_chunk(db, editor, fragment_id, 'editor')['text'] == 'Private metadata'
        schema = db.get(FormSchema, shared.schema_version_id).schema_data
        assert display_values(db, schema, shared.custom_data, editor, 'editor')['linked'] == private.title
        assert display_values(db, schema, shared.custom_data, editor, 'viewer')['linked'] == 'Недоступный документ'
        assert summaries(db, editor, [shared], 'viewer')[shared.id]['display_values']['linked'] == 'Недоступный документ'
        relation = SimpleNamespace(source_document_id=shared.id, target_document_id=private.id)
        calls = [
            lambda: read_chunk(db, editor, fragment_id, 'viewer'),
            lambda: options(private.id, db, editor, mode='viewer'),
            lambda: graph(private.id, db, editor, mode='viewer'),
            lambda: endpoints(db, editor, relation, 'viewer'),
            lambda: list(visible_relations(db, editor, private.id, 'viewer')),
            lambda: basis(db, editor, {'document_id': str(private.id)}, 'viewer'),
            lambda: related(db, editor, document_id=private.id, mode='viewer'),
        ]
        for call in calls:
            with pytest.raises(HTTPException) as denied:
                call()
            assert denied.value.status_code == 404
        instance = SimpleNamespace(snapshot={'steps': [{'key': 'step'}]})
        result = SimpleNamespace(
            id=uuid4(), values={}, comment='', attachments=[], actor_id=editor.id,
            created_at=datetime.now(UTC), result_document={
                'document_id': str(private.id), 'schema_version_id': str(private.schema_version_id),
            },
        )
        attempt = SimpleNamespace(step_key='step')
        assert result_output(db, editor, instance, attempt, result, 'editor')['result_document']['title'] == private.title
        visible = result_output(db, editor, instance, attempt, result, 'viewer')
        assert visible['result_document'] == {'restricted': True, 'label': 'Документ результата ограничен'}
        assert visible['values'] == {}
