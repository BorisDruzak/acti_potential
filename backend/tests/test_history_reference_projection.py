from copy import deepcopy
from uuid import UUID, uuid4

from app.registry.models import (
    Document,
    DocumentType,
    FormSchema,
    MetadataRevision,
    Registry,
    RegistryAccess,
)
from app.registry.validation import validate_schema
from tests.conftest import login_admin


def test_history_projects_old_selections_before_conditions_without_rewriting_snapshots(client, factory):
    login_admin(client, factory)
    actor_id = UUID(client.get("/api/v1/users/me").json()["id"])
    person = str(uuid4())
    fields = [{"key": "person", "type": "multi_reference", "label": "Исполнитель", "reference_code": "executors"}]
    for op in ("eq", "ne", "in", "not_in"):
        fields.append({"key": op, "type": "text", "label": op, "visible_when": {
            "field": "person", "op": op, "value": [[person]] if op in {"in", "not_in"} else [person]}})
    fields.append({"key": "group", "type": "group", "label": "Группа", "children": [
        {"key": "participant", "type": "multi_reference", "label": "Согласующий", "reference_code": "approvers"}]})
    schema_data = validate_schema({"sections": [{"key": "main", "title": "Сведения", "fields": fields}]})
    snapshot = {"person": person, "eq": "visible eq", "in": "visible in", "ne": "hidden ne",
                "not_in": "hidden not_in", "group": [{"participant": person}]}
    original = deepcopy(snapshot)
    with factory.begin() as db:
        registry = Registry(code="history-people", name="Synthetic history")
        kind = DocumentType(code="history-people", name="Synthetic history")
        db.add_all([registry, kind])
        db.flush()
        schema = FormSchema(document_type_id=kind.id, version=1, state="published", schema_data=schema_data)
        db.add(schema)
        db.flush()
        document = Document(registry_id=registry.id, document_type_id=kind.id, schema_version_id=schema.id,
                            created_by=actor_id, custom_data={"person": [person]}, revision=2)
        db.add_all([document, RegistryAccess(registry_id=registry.id, user_id=actor_id, permissions=["read"])])
        db.flush()
        revision = MetadataRevision(document_id=document.id, revision=1, values=snapshot, created_by=actor_id)
        db.add(revision)
        db.flush()
        document_id, revision_id = document.id, revision.id
    response = client.get(f"/api/v1/documents/{document_id}/history")
    assert response.status_code == 200, response.text
    assert response.json()[0]["values"] == {"person": [person], "eq": "visible eq", "in": "visible in",
                                           "group": [{"participant": [person]}]}
    with factory() as db:
        assert db.get(MetadataRevision, revision_id).values == original
        assert db.get(Document, document_id).revision == 2
