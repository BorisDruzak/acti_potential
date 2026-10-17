from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text

from app.registry.models import (
    Document,
    DocumentACL,
    FormSchema,
    MetadataRevision,
    ReferenceItem,
    ReferenceSet,
    Registry,
    RegistryAccess,
)
from tests.test_content_security import actors_and_document, sign_in_as
from tests.test_task009_execution import action, current


def execution_people_sample(client, factory):
    person_id = str(uuid4())
    sample = actors_and_document(factory, fields=[
        {"key": "title", "label": "Title", "type": "text", "system": "title"},
        {"key": "person", "label": "Executor", "type": "reference", "reference_code": "executors", "required": True},
        {"key": "conditional", "label": "Conditional result", "type": "text",
         "visible_when": {"field": "person", "op": "eq", "value": person_id}},
    ])
    with factory.begin() as db:
        document = db.get(Document, sample["document"])
        schema_id, dtype = str(document.schema_version_id), str(document.document_type_id)
        db.get(Registry, sample["registry"]).document_type_ids = [dtype]
        db.add(RegistryAccess(registry_id=sample["registry"], user_id=sample["admin"].id,
                              permissions=["read", "create", "update"]))
        reference = ReferenceSet(registry_id=sample["registry"], code="executors", name="Executors")
        db.add(reference)
        db.flush()
        person = ReferenceItem(id=UUID(person_id), reference_set_id=reference.id, label="Synthetic executor")
        db.add(person)
        db.flush()
        document.custom_data = {"person": person_id, "conditional": "Synthetic conditional evidence"}
        db.add(MetadataRevision(document_id=document.id, revision=document.revision,
                                values={"title": document.title, **document.custom_data},
                                created_by=document.created_by))
    headers = sign_in_as(client, factory, sample["admin"])
    process = client.post("/api/v1/processes", headers=headers, json={
        "registry_id": str(sample["registry"]), "code": "synthetic-people-execution",
        "name": "Synthetic people execution", "purpose": "Migration compatibility",
    })
    assert process.status_code == 201, process.text
    draft = client.get(f"/api/v1/processes/{process.json()['id']}/versions").json()[0]
    saved = client.put(f"/api/v1/process-versions/{draft['id']}", headers=headers, json={
        "revision": draft["revision"], "effective_from": "2026-01-01",
        "steps": [{"key": "start", "title": "Start", "is_start": True, "form_schema_id": schema_id},
                  {"key": "complete", "title": "Complete", "is_terminal": True}],
        "transitions": [{"key": "finish", "from_step": "start", "to_step": "complete"}],
    })
    assert saved.status_code == 200, saved.text
    published = client.post(f"/api/v1/process-versions/{draft['id']}/publish", headers=headers,
                            json={"revision": saved.json()["revision"]})
    assert published.status_code == 200, published.text
    headers = sign_in_as(client, factory, sample["editor"])
    instances = []
    for name in ["Synthetic pending result", "Synthetic confirmed result"]:
        started = client.post("/api/v1/execution/instances", headers=headers, json={
            "process_version_id": published.json()["id"], "name": name,
        })
        assert started.status_code == 201, started.text
        instances.append(action(client, headers, started.json(), "open"))
    supplied = {"document_id": str(sample["document"]), "revision": 1}
    instances[1] = action(client, headers, instances[1], "result", result_document=supplied)
    result_before = deepcopy(current(instances[1])["result"])
    instances[1] = action(client, headers, instances[1], "advance")
    return sample, headers, instances, supplied, schema_id, result_before, person_id


def migrate_people(factory):
    path = Path(__file__).resolve().parents[1] / "migrations/versions/0014_people_selection.py"
    spec = spec_from_file_location("people_execution_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    with factory.kw["bind"].begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()


def test_pinned_pending_and_completed_document_results_survive_people_migration(client, factory):
    sample, headers, instances, supplied, original_id, result_before, person = execution_people_sample(client, factory)
    with factory() as db:
        snapshots = {table: db.execute(text(f"SELECT * FROM {table}")).mappings().all()
                     for table in ["process_versions", "process_steps", "step_results", "execution_instances",
                                   "step_executions", "execution_events"]}
    before_listing = client.get("/api/v1/execution/instances").json()
    migrate_people(factory)
    with factory() as db:
        document = db.get(Document, sample["document"])
        assert str(document.schema_version_id) != original_id
        assert document.custom_data["person"] == [person]
        for table, before in snapshots.items():
            assert db.execute(text(f"SELECT * FROM {table}")).mappings().all() == before
    after_listing = client.get("/api/v1/execution/instances").json()
    assert after_listing["total"] == before_listing["total"] == 2
    assert [(row["id"], row["state"]) for row in after_listing["items"]] == [
        (row["id"], row["state"]) for row in before_listing["items"]
    ]
    completed = client.get(f"/api/v1/execution/instances/{instances[1]['id']}")
    assert completed.status_code == 200, completed.text
    previous = next(step for step in completed.json()["steps"] if step["step_key"] == "start")["result"]
    assert previous == result_before
    assert previous["result_document"]["schema_version_id"] == original_id
    pending = action(client, headers, instances[0], "result", result_document=supplied)
    assert current(pending)["result"]["values"]["person"] == person
    assert not current(pending)["result"]["result_document"].get("restricted")
    updated = client.put(f"/api/v1/documents/{sample['document']}", headers=headers, json={
        "revision": 1, "values": {"person": [person]},
    })
    assert updated.status_code == 200, updated.text
    projected = action(client, headers, pending, "result", result_document={
        **supplied, "revision": updated.json()["revision"],
    })
    assert current(projected)["result"]["values"]["person"] == person
    assert current(projected)["result"]["values"]["conditional"] == "Synthetic conditional evidence"
    action(client, headers, projected, "result", expected=409, result_document=supplied)
    with factory() as db:
        stored = db.scalar(select(MetadataRevision).where(
            MetadataRevision.document_id == sample["document"],
            MetadataRevision.revision == updated.json()["revision"],
        ))
        assert stored.values["person"] == [person]


@pytest.mark.parametrize("restriction", ["unrelated-version", "current-acl"])
def test_people_lineage_never_bypasses_unrelated_forms_or_current_document_acl(client, factory, restriction):
    sample, headers, instances, supplied, _, _, _ = execution_people_sample(client, factory)
    migrate_people(factory)
    with factory.begin() as db:
        document = db.get(Document, sample["document"])
        if restriction == "unrelated-version":
            original = db.get(FormSchema, document.schema_version_id)
            maximum = max(db.scalars(select(FormSchema.version).where(
                FormSchema.document_type_id == document.document_type_id)))
            unrelated = FormSchema(document_type_id=document.document_type_id, version=maximum + 1,
                                   state="published", schema_data=deepcopy(original.schema_data))
            db.add(unrelated)
            db.flush()
            document.schema_version_id = unrelated.id
        else:
            db.add(DocumentACL(document_id=document.id, user_id=sample["admin"].id, permissions=["read"]))
    response = client.post(f"/api/v1/execution/instances/{instances[0]['id']}/result", headers=headers,
                           json={"revision": instances[0]["revision"], "result_document": supplied})
    assert response.status_code == (422 if restriction == "unrelated-version" else 404), response.text
    completed = client.get(f"/api/v1/execution/instances/{instances[1]['id']}")
    assert completed.status_code == 200, completed.text
    previous = next(step for step in completed.json()["steps"] if step["step_key"] == "start")["result"]
    assert previous["result_document"]["restricted"] is True
    assert previous["values"] == {}
    with factory() as db:
        from app.execution.models import ProcessInstance

        assert db.get(ProcessInstance, UUID(instances[0]["id"])).revision == instances[0]["revision"]


def test_pinned_scalar_process_rejects_lossy_multi_person_projection(client, factory):
    sample, headers, instances, supplied, _, _, person = execution_people_sample(client, factory)
    migrate_people(factory)
    with factory.begin() as db:
        reference_id = db.scalar(select(ReferenceSet.id).where(
            ReferenceSet.registry_id == sample["registry"], ReferenceSet.code == "executors"))
        second = ReferenceItem(reference_set_id=reference_id, label="Synthetic second executor")
        db.add(second)
        db.flush()
        second_id = str(second.id)
    updated = client.put(f"/api/v1/documents/{sample['document']}", headers=headers, json={
        "revision": 1, "values": {"person": [person, second_id]},
    })
    assert updated.status_code == 200, updated.text
    response = client.post(f"/api/v1/execution/instances/{instances[0]['id']}/result", headers=headers,
                           json={"revision": instances[0]["revision"], "result_document": {
                               **supplied, "revision": updated.json()["revision"],
                           }})
    assert response.status_code == 422, response.text
    assert "одного сотрудника" in response.json()["error"]["message"]
    assert client.get(f"/api/v1/documents/{sample['document']}").json()["values"]["person"] == [person, second_id]
    unchanged = client.get(f"/api/v1/execution/instances/{instances[0]['id']}").json()
    assert unchanged["revision"] == instances[0]["revision"]
    assert current(unchanged)["result"] is None


def test_runtime_compatibility_transform_matches_frozen_migration_without_modifying_input():
    from app.registry.schema_compatibility import _people_schema_0014

    path = Path(__file__).resolve().parents[1] / "migrations/versions/0014_people_selection.py"
    spec = spec_from_file_location("people_compatibility_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    first, second, reference_id = str(uuid4()), str(uuid4()), str(uuid4())
    schema = {"sections": [{"key": "main", "title": "ACTI v2", "fields": [
        {"key": "person", "label": "ACTI v2", "type": "reference", "reference_set_id": reference_id,
         "default": first, "read_roles": ["admin"], "write_roles": ["admin"]},
        {"key": "conditional", "label": "Conditional", "type": "text",
         "visible_when": {"field": "person", "op": "in", "value": [first, second]}},
        {"key": "team", "label": "Team", "type": "group", "children": [
            {"key": "signer", "label": "Signer", "type": "reference", "reference_code": "signers", "default": first}
        ], "default": [{"signer": first}, {"signer": [first, second, first]}]},
    ]}], "list_view": {"columns": [{"key": "person", "label": "ACTI-v2"}]}}
    before = deepcopy(schema)
    assert _people_schema_0014(schema, {reference_id}) == migration.upgrade_schema(schema, {reference_id})
    assert schema == before


@pytest.mark.parametrize("change", ["matching", "random-id", "read-acl", "different-type", "draft"])
def test_compatibility_requires_exact_migration_lineage_and_complete_schema_equivalence(factory, change):
    from uuid import NAMESPACE_URL, uuid5

    from app.registry.models import DocumentType
    from app.registry.schema_compatibility import compatible_people_schema

    with factory.begin() as db:
        kind = DocumentType(code="people-compat", name="Synthetic compatibility")
        other_kind = DocumentType(code="people-compat-other", name="Synthetic other type")
        db.add_all([kind, other_kind])
        db.flush()
        original = FormSchema(document_type_id=kind.id, version=1, state="published", schema_data={
            "sections": [{"key": "main", "title": "Main", "fields": [
                {"key": "person", "label": "Person", "type": "reference", "reference_code": "executors"},
            ]}],
        })
        db.add(original)
        db.flush()
        upgraded = deepcopy(original.schema_data)
        upgraded["sections"][0]["fields"][0]["type"] = "multi_reference"
        if change == "read-acl":
            upgraded["sections"][0]["fields"][0]["read_roles"] = ["admin"]
        clone = FormSchema(id=uuid4() if change == "random-id" else uuid5(NAMESPACE_URL, f"acti:people-selection:0014:{original.id}"),
                           document_type_id=other_kind.id if change == "different-type" else kind.id,
                           version=2, state="draft" if change == "draft" else "published", schema_data=upgraded)
        db.add(clone)
        db.flush()
        assert compatible_people_schema(db, clone.id, original.id) is (change == "matching")
