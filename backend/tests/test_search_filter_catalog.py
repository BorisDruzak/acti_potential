"""Filter predicates retain schema identity and card publication boundaries."""
import json
from uuid import uuid4

from app.content.contracts import SearchInput
from app.registry.document_search import SearchParameters, search_query
from app.registry.models import Document, FormSchema, Registry
from tests.test_card_publication import publication_cards
from tests.test_content_security import actors_and_document, sign_in_as


def test_publication_target_filter_uses_the_same_acl_for_search_and_export(client, factory):
    sample = publication_cards(factory, fields=[{"key": "title", "label": "Название", "type": "text", "system": "title", "exportable": True}])
    for actor, target, expected in [("editor", "internal", 2), ("editor", "shared", 2), ("viewer", "internal", 1), ("viewer", "shared", 1)]:
        headers = sign_in_as(client, factory, sample[actor])
        params = {"publication_target": target, "mode": "editor"}
        response = client.post("/api/v1/search", headers=headers, json=params)
        assert response.status_code == 200, response.text
        assert response.json()["total"] == expected
        exported = client.get("/api/v1/search/export", params={"parameters": json.dumps(params)})
        assert exported.status_code == 200, exported.text
        assert len(exported.content.decode("utf-8-sig").splitlines()) == expected + 1
    assert SearchInput(publication_target="shared").legacy_filters().publication_target == "shared"
    with factory() as db:
        query, _ = search_query(db, sample["viewer"], SearchParameters(publication_target="internal"))
        assert list(db.scalars(query.with_only_columns(Document.id))) == [sample["shared"]]


def test_scoped_field_keys_never_match_a_different_published_schema(client, factory):
    sample = actors_and_document(factory, fields=[{"key": "same", "label": "Старый смысл", "type": "text", "filterable": True}])
    with factory.begin() as db:
        source = db.get(Document, sample["document"])
        source.custom_data = {"same": "common"}
        db.get(Registry, sample["registry"]).document_type_ids = [str(source.document_type_id)]
        version = FormSchema(document_type_id=source.document_type_id, version=2, state="published", schema_data={"sections": [{"key": "s", "title": "S", "fields": [{"key": "same", "label": "Другой смысл", "type": "text", "filterable": True}, {"key": "secret", "label": "Скрыто", "type": "text", "filterable": True, "read_roles": ["admin"]}]}]})
        db.add(version)
        db.flush()
        other = Document(registry_id=source.registry_id, document_type_id=source.document_type_id, schema_version_id=version.id, created_by=source.created_by, card_state="published", publication_targets=["shared"], custom_data={"same": "common", "secret": "hidden"})
        db.add(other)
        db.flush()
        first_schema, second_schema, second_id = source.schema_version_id, version.id, other.id
    headers = sign_in_as(client, factory, sample["viewer"])
    for schema, identity in [(first_schema, sample["document"]), (second_schema, second_id)]:
        response = client.post("/api/v1/search", headers=headers, json={"filters": {f"schema:{schema}:same": "common"}})
        assert response.status_code == 200, response.text
        assert [hit["document"]["id"] for hit in response.json()["items"]] == [str(identity)]
    for key in [f"schema:{second_schema}:secret", f"schema:{uuid4()}:same", "schema:invalid:same"]:
        assert client.post("/api/v1/search", headers=headers, json={"filters": {key: "hidden"}}).status_code == 422


def test_publication_boolean_true_false_and_unspecified_are_distinct(client, factory):
    sample = actors_and_document(factory, fields=[{"key": "should_publish", "label": "Подлежит опубликованию", "type": "boolean", "filterable": True}])
    with factory.begin() as db:
        source = db.get(Document, sample["document"])
        source.custom_data = {"should_publish": True}
        for value in [False, None]:
            db.add(Document(registry_id=source.registry_id, document_type_id=source.document_type_id, schema_version_id=source.schema_version_id, created_by=source.created_by, card_state="published", publication_targets=["shared"], custom_data={"should_publish": value}))
    headers = sign_in_as(client, factory, sample["viewer"])
    for value in [True, False, {"op": "absent"}]:
        response = client.post("/api/v1/search", headers=headers, json={"filters": {"should_publish": value}})
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1
