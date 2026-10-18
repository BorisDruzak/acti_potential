from copy import deepcopy
from datetime import date
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, select, text
from sqlalchemy.orm import Session

from app.content.models import MetadataFragment
from app.identity.models import User
from app.knowledge.models import Template, TemplateVersion
from app.processes.models import Process, ProcessVersion
from app.registry.builtin import BUILTIN_SCHEMA, seed_builtin
from app.registry.models import (
    Document,
    DocumentACL,
    DocumentType,
    FormSchema,
    MetadataRevision,
    ReferenceItem,
    ReferenceSet,
    Registry,
    RegistryAccess,
)
from app.registry.validation import validate_schema
from tests.conftest import login_admin


def test_builtin_form_engine_accepts_multiple_selected_people_as_defaults():
    schema = deepcopy(BUILTIN_SCHEMA)
    for section in schema["sections"]:
        for field in section["fields"]:
            if field["key"] in {"executor_id", "responsible_executor_id"}:
                field["default"] = [str(uuid4()), str(uuid4())]
    assert validate_schema(schema) == schema


def test_schema_upgrade_keeps_nonempty_captions_when_legacy_marker_is_the_entire_label():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/0014_people_selection.py"
    spec = spec_from_file_location("people_selection_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    schema = {"sections": [{"key": "main", "title": "ACTI v2", "fields": [
        {"key": "person", "label": "ACTI v2", "type": "reference", "reference_code": "executors"}
    ]}]}
    upgraded = migration.upgrade_schema(schema, set())
    assert upgraded["sections"][0]["title"] == "Документы"
    assert upgraded["sections"][0]["fields"][0]["label"] == "Документы"
    assert validate_schema(upgraded) == upgraded


def test_new_builtin_cards_accept_multiple_executors_and_responsible_people(factory):
    with factory.begin() as db:
        seed_builtin(db)
        kind = db.scalar(select(DocumentType).where(DocumentType.code == "acti-v2"))
        schema = db.scalar(select(FormSchema).where(FormSchema.document_type_id == kind.id))
        fields = {f["key"]: f for s in schema.schema_data["sections"] for f in s["fields"]}
        assert fields["executor_id"]["type"] == "multi_reference"
        assert fields["responsible_executor_id"]["type"] == "multi_reference"
        assert fields["type_id"]["label"] == "Тип документа"
        assert kind.name == "Нормативные и распорядительные документы"
        assert set(db.scalars(select(ReferenceSet.name).where(
            ReferenceSet.code == "document_types"
        ))) == {"Типы документов"}


def test_people_upgrade_clones_published_forms_preserving_current_values_and_history(factory):
    engine = factory.kw["bind"]
    with engine.begin() as conn:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = conn
        command.downgrade(config, "0013_card_publication")
        old_documents = Table("documents", MetaData(), autoload_with=conn)
        first, second = str(uuid4()), str(uuid4())
        with Session(bind=conn, join_transaction_mode="create_savepoint") as db, db.begin():
            user = User(display_name="Synthetic people migration owner")
            registry = Registry(code="people-migration", name="Synthetic migration")
            kind = DocumentType(code="acti-v2", name="ACTI v2 — нормативные и распорядительные документы")
            db.add_all([user, registry, kind])
            db.flush()
            reference = ReferenceSet(registry_id=registry.id, code="responsible_executors", name="People")
            db.add(reference)
            db.flush()
            reference_id = str(reference.id)
            old = validate_schema({"sections": [{"key": "main", "title": "Main", "fields": [
                {"key": "executor_id", "label": "Исполнитель", "type": "reference",
                 "reference_code": "executors", "default": first, "read_roles": ["admin"],
                 "write_roles": ["admin"]},
                {"key": "conditional", "label": "Conditional", "type": "text",
                 "visible_when": {"field": "executor_id", "op": "eq", "value": first}},
                {"key": "responsible", "label": "Responsible", "type": "reference",
                 "reference_set_id": reference_id},
                {"key": "type_id", "label": "Тип документа ACTI v2", "type": "reference",
                 "reference_code": "document_types"},
                {"key": "status", "label": "Status", "type": "reference", "reference_code": "status"},
                {"key": "group", "label": "Group", "type": "group", "children": [
                    {"key": "person", "label": "Person", "type": "reference", "reference_code": "approvers"}
                ], "default": [{"person": first}]},
            ]}], "list_view": {"columns": [{"key": "type_id", "label": "Тип документа ACTI v2"}]}})
            schemas = [FormSchema(document_type_id=kind.id, version=version, state=state,
                                  schema_data=deepcopy(old))
                       for version, state in [(1, "published"), (2, "draft"), (4, "published")]]
            db.add_all(schemas)
            db.flush()
            latest_data = deepcopy(old)
            latest_fields = latest_data["sections"][0]["fields"]
            latest_fields[0]["type"], latest_fields[0]["default"] = "multi_reference", [first]
            latest_fields[1]["visible_when"]["value"] = [first]
            latest_fields[2]["type"] = "multi_reference"
            latest_fields[3]["label"] = "Тип документа"
            latest_fields[5]["children"][0]["type"] = "multi_reference"
            latest_fields[5]["default"] = [{"person": [first]}]
            latest_fields.append({"key": "newest", "label": "Newest field", "type": "text"})
            latest_data["list_view"]["columns"][0]["label"] = "Тип документа"
            newest = FormSchema(document_type_id=kind.id, version=8, state="published",
                                schema_data=validate_schema(latest_data))
            db.add(newest)
            values = {"executor_id": first, "responsible": [second, first, second],
                      "group": [{"person": first}, {"person": [second, first, second]}],
                      "status": first, "unmapped": {"preserved": True}}
            doc_id = uuid4()
            # Populate the historical schema under test without assuming today's columns.
            conn.execute(old_documents.insert().values(
                id=doc_id, registry_id=registry.id, document_type_id=kind.id,
                schema_version_id=schemas[0].id, created_by=user.id,
                custom_data=deepcopy(values), revision=7, current_version=2,
                title="Synthetic selection", source_database="okrug",
                source_entity="documents", source_id="synthetic-only",
            ))
            db.add_all([
                MetadataRevision(document_id=doc_id, revision=7, values=deepcopy(values), created_by=user.id),
                DocumentACL(document_id=doc_id, user_id=user.id, permissions=["read"]),
                MetadataFragment(document_id=doc_id, document_revision=7, field_key="type_id",
                                 label="Тип документа ACTI v2", text="Synthetic classification"),
            ])
            kind_id = kind.id
            schema_ids = [schema.id for schema in schemas]
            process = Process(registry_id=registry.id, code="synthetic-pinned",
                              metadata_data={}, created_by=user.id)
            template = Template(registry_id=registry.id, owner_id=user.id, created_by=user.id)
            db.add_all([process, template])
            db.flush()
            pinned = {"schema_version_id": str(schema_ids[0]), "values": {"executor_id": first}}
            db.add_all([
                ProcessVersion(process_id=process.id, version=1, state="published",
                               effective_from=date(2026, 1, 1), created_by=user.id,
                               metadata_snapshot=deepcopy(pinned)),
                TemplateVersion(template_id=template.id, version=1, state="published",
                                data=deepcopy(pinned), text="Synthetic pinned template"),
            ])
        untouched = ["metadata_revisions", "document_acl", "audit_events", "outbox_events",
                     "number_entries", "document_files", "document_versions", "content_index_states",
                     "process_versions", "knowledge_template_versions"]
        snapshots = {table: conn.execute(text(f"SELECT * FROM {table}")).mappings().all() for table in untouched}
        original_document = dict(conn.execute(text("SELECT * FROM documents WHERE id=:id"), {"id": doc_id}).mappings().one())
        original_fragment = dict(conn.execute(text("SELECT * FROM content_metadata_fragments")).mappings().one())
        command.upgrade(config, "head")
        with Session(bind=conn) as db:
            document = db.get(Document, doc_id)
            assert document.schema_version_id != schema_ids[0]
            assert document.custom_data == {**values, "executor_id": [first], "responsible": [second, first],
                                            "group": [{"person": [first]}, {"person": [second, first]}]}
            for schema_id in (schema_ids[0], schema_ids[2]):
                assert db.get(FormSchema, schema_id).schema_data == old
            current = db.get(FormSchema, document.schema_version_id)
            upgraded = current.schema_data
            assert validate_schema(upgraded) == upgraded
            fields = upgraded["sections"][0]["fields"]
            assert fields[0] == {**old["sections"][0]["fields"][0], "type": "multi_reference", "default": [first]}
            assert fields[1]["visible_when"] == {"field": "executor_id", "op": "eq", "value": [first]}
            assert fields[2]["reference_set_id"] == reference_id
            assert fields[2]["type"] == "multi_reference"
            assert fields[4]["type"] == "reference"
            assert fields[5]["default"] == [{"person": [first]}]
            assert db.get(FormSchema, schema_ids[1]).schema_data == upgraded
            assert upgraded["list_view"]["columns"][0]["label"] == "Тип документа"
            all_schemas = list(db.scalars(select(FormSchema).where(FormSchema.document_type_id == kind_id)))
            assert len(all_schemas) == 7
            assert sorted(s.version for s in all_schemas) == [1, 2, 4, 8, 9, 10, 11]
            assert max(all_schemas, key=lambda s: s.version).schema_data == latest_data
        after_document = dict(conn.execute(text("SELECT * FROM documents WHERE id=:id"), {"id": doc_id}).mappings().one())
        assert {k: after_document[k] for k in original_document if k not in {"schema_version_id", "custom_data"}} == {
            k: v for k, v in original_document.items() if k not in {"schema_version_id", "custom_data"}
        }
        assert dict(conn.execute(text("SELECT * FROM content_metadata_fragments")).mappings().one()) == {
            **original_fragment, "label": "Тип документа"
        }
        for table, before in snapshots.items():
            assert conn.execute(text(f"SELECT * FROM {table}")).mappings().all() == before
        command.upgrade(config, "head")
        assert conn.scalar(text("SELECT count(*) FROM form_schemas")) == 7
        with pytest.raises(RuntimeError, match="pre-upgrade backup"), conn.begin_nested():
            command.downgrade(config, "0013_card_publication")
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0018_draft_registration_history"


def test_people_arrays_round_trip_and_stale_scalars_or_foreign_registry_ids_do_not_persist(client, factory):
    assert login_admin(client, factory).status_code == 200
    csrf = {"Origin": "https://testserver",
            "X-CSRF-Token": client.get("/api/v1/users/me").json()["csrf_token"]}
    actor_id = client.get("/api/v1/users/me").json()["id"]
    with factory.begin() as db:
        seed_builtin(db)
        registries = list(db.scalars(select(Registry).order_by(Registry.code)))
        registry, foreign_registry = registries
        db.add(RegistryAccess(registry_id=registry.id, user_id=actor_id,
                              permissions=["read", "create", "update"]))
        kind = db.scalar(select(DocumentType).where(DocumentType.code == "acti-v2"))
        values = {}
        for code, key in [("executors", "executor_id"), ("responsible_executors", "responsible_executor_id")]:
            reference = db.scalar(select(ReferenceSet).where(ReferenceSet.registry_id == registry.id,
                                                            ReferenceSet.code == code))
            entries = [ReferenceItem(reference_set_id=reference.id, label=f"Synthetic {code} {i}")
                       for i in range(2)]
            db.add_all(entries)
            db.flush()
            values[key] = [str(item.id) for item in entries]
        other_reference = db.scalar(select(ReferenceSet).where(ReferenceSet.registry_id == foreign_registry.id,
                                                              ReferenceSet.code == "executors"))
        foreign = ReferenceItem(reference_set_id=other_reference.id, label="Synthetic foreign person")
        db.add(foreign)
        db.flush()
        registry_id, type_id, foreign_id = str(registry.id), str(kind.id), str(foreign.id)
    response = client.post("/api/v1/documents", json={"registry_id": registry_id,
        "document_type_id": type_id, "values": {"title": "Synthetic people card", **values}}, headers=csrf)
    assert response.status_code == 201, response.text
    document = response.json()
    document_id = document["id"]
    assert document["values"]["executor_id"] == values["executor_id"]
    assert document["values"]["responsible_executor_id"] == values["responsible_executor_id"]
    with factory.begin() as db:
        from uuid import UUID

        db.get(ReferenceItem, UUID(values["executor_id"][0])).active = False
    for rejected in [values["executor_id"][0], [foreign_id]]:
        failed = client.put(f"/api/v1/documents/{document_id}", json={
            "revision": document["revision"], "values": {"executor_id": rejected}}, headers=csrf)
        assert failed.status_code == 422, failed.text
        unchanged = client.get(f"/api/v1/documents/{document_id}").json()
        assert unchanged["revision"] == document["revision"]
        assert unchanged["values"] == document["values"]
    changed = client.put(f"/api/v1/documents/{document_id}", json={
        "revision": document["revision"], "values": {"executor_id": values["executor_id"][::-1]}}, headers=csrf)
    assert changed.status_code == 200, changed.text
    assert changed.json()["values"]["executor_id"] == values["executor_id"][::-1]
