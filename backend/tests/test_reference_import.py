from copy import deepcopy
from uuid import uuid4

import pytest
from migration.reference_import import TABLES, SnapshotError, plan_snapshot


def snapshot():
    return {"version": 1, "sources": [
        {"database": database, "registry_id": str(uuid4()), "tables": {
            table: [{"id": 1, "name": "Synthetic", "is_active": False,
                     "position": None, "department": "Synthetic unit", "extra": {"x": 1}}]
            for table in TABLES
        }} for database in ("okrug", "raion")
    ]}


def test_snapshot_retains_every_source_identity_inactive_row_and_attribute():
    value = snapshot()
    plan = plan_snapshot(value)
    assert len(plan["items"]) == 20
    assert len({row["id"] for row in plan["items"]}) == 20
    assert all(not row["active"] for row in plan["items"])
    assert all(row["metadata"]["source_attributes"]["position"] is None for row in plan["items"])
    assert {r["source_database"] for r in plan["items"]} == {"okrug", "raion"}
    assert plan_snapshot(deepcopy(value))["fingerprint"] == plan["fingerprint"]
    value["sources"][0]["tables"]["dbo.ref_executors"][0]["department"] = "Changed"
    assert plan_snapshot(value)["fingerprint"] != plan["fingerprint"]


def test_snapshot_order_does_not_change_fingerprint_or_ids():
    value = snapshot()
    original = plan_snapshot(value)
    value["sources"].reverse()
    for source in value["sources"]:
        source["tables"] = dict(reversed(list(source["tables"].items())))
    assert plan_snapshot(value) == original


def test_snapshot_rejects_omitted_dictionaries_shared_registry_and_invalid_rows_together():
    value = snapshot()
    del value["sources"][0]["tables"]["dbo.ref_status"]
    value["sources"][1]["registry_id"] = value["sources"][0]["registry_id"]
    rows = value["sources"][1]["tables"]["dbo.ref_executors"]
    rows[0]["name"] = "x" * 256
    rows.append({"id": 2, "name": None})
    with pytest.raises(SnapshotError) as error:
        plan_snapshot(value)
    assert error.value.errors["missing_tables"] == 1
    assert error.value.errors["shared_registry"] == 1
    assert error.value.errors["invalid_label"] == 2
    assert "Synthetic" not in str(error.value)

def test_snapshot_validates_duplicate_labels_and_source_ids_without_exposing_values():
    value = snapshot()
    table = value["sources"][0]["tables"]["dbo.ref_status"]
    table.append(dict(table[0]))
    with pytest.raises(SnapshotError) as error:
        plan_snapshot(value)
    assert error.value.errors == {"duplicate_label": 1, "duplicate_source_id": 1}


def test_snapshot_explicit_seed_adoption_is_bound_into_exact_plan_fingerprint():
    value = snapshot()
    plan = plan_snapshot(value)
    target_id = str(uuid4())
    value["sources"][0]["adopt"] = {"dbo.ref_status": {"1": target_id}}
    adopted = plan_snapshot(value)
    assert plan["fingerprint"] != adopted["fingerprint"]
    row = next(item for item in adopted["items"]
               if item["source_database"] == "okrug" and item["source_entity"] == "dbo.ref_status")
    assert row["adopt_id"] == target_id

@pytest.mark.parametrize("database", ["okrug", "raion"])
def test_blank_source_label_gets_stable_display_without_losing_original(database):
    from migration.reference_import import fingerprint

    value = snapshot()
    source = next(source for source in value["sources"] if source["database"] == database)
    raw = source["tables"]["dbo.ref_executors"][0]
    raw["name"] = ""
    original = deepcopy(value)
    plan = plan_snapshot(value)
    row = next(item for item in plan["items"]
               if item["source_database"] == database and item["source_entity"] == "dbo.ref_executors")
    assert row["label"] == f"Без наименования [{database}/ref_executors/1]"
    assert row["metadata"]["missing_source_label"] is True
    assert row["metadata"]["source_attributes"] == raw
    assert row["metadata"]["source_attributes"]["name"] == ""
    assert row["source_fingerprint"] == fingerprint(raw)
    assert row["active"] is False
    assert len(plan["items"]) == 20
    assert value == original
    repeated = plan_snapshot(deepcopy(value))
    assert repeated == plan


def test_blank_source_label_fallback_is_bounded_and_qualified_by_source_identity():
    value = snapshot()
    for source in value["sources"]:
        source["tables"]["dbo.ref_status"] = [
            {"id": "s" * 255, "name": ""},
            {"id": "t" * 255, "name": ""},
        ]
    plan = plan_snapshot(value)
    rows = [item for item in plan["items"] if item["source_entity"] == "dbo.ref_status"]
    assert len(rows) == 4
    assert len({row["label"] for row in rows}) == 4
    assert all(0 < len(row["label"]) <= 255 for row in rows)
    assert all(row["metadata"]["source_attributes"]["name"] == "" for row in rows)

def test_empty_source_name_marker_tracks_later_correction_without_changing_uuid(factory):
    from uuid import UUID

    from migration.reference_import import target_plan
    from sqlalchemy import select

    from app.registry.models import ReferenceItem, Registry

    value = snapshot()
    raw = value["sources"][0]["tables"]["dbo.ref_status"][0]
    raw["name"] = ""
    blank_plan = plan_snapshot(value)
    with factory.begin() as session:
        for source in value["sources"]:
            session.add(Registry(id=UUID(source["registry_id"]), code=source["database"],
                                 name="Synthetic registry", document_type_ids=[]))
    with factory.begin() as session:
        target_plan(session, blank_plan, apply=True)
        row = session.scalar(select(ReferenceItem).where(
            ReferenceItem.source_database == "okrug", ReferenceItem.source_entity == "dbo.ref_status"))
        identity = row.id
        assert row.item_metadata["source_attributes"]["name"] == ""
        assert row.item_metadata["missing_source_label"] is True
    raw["name"] = "Synthetic recovered label"
    corrected_plan = plan_snapshot(value)
    with factory.begin() as session:
        report = target_plan(session, corrected_plan, apply=True)
        assert report["updated"] == 1
        row = session.get(ReferenceItem, identity)
        assert row.label == "Synthetic recovered label"
        assert row.item_metadata["source_attributes"]["name"] == "Synthetic recovered label"
        assert "missing_source_label" not in row.item_metadata

@pytest.mark.parametrize("clear_mode", ["null", "absent"])
def test_source_owned_participant_metadata_clears_and_retains_manual_attributes(factory, clear_mode):
    from uuid import UUID

    from migration.reference_import import target_plan
    from sqlalchemy import select

    from app.registry.models import ReferenceItem, Registry

    value = snapshot()
    raw = value["sources"][0]["tables"]["dbo.ref_executors"][0]
    raw.update(position="Synthetic original position", department="Synthetic original unit")
    with factory.begin() as session:
        for source in value["sources"]:
            session.add(Registry(id=UUID(source["registry_id"]), code=source["database"],
                                 name="Synthetic registry", document_type_ids=[]))
    with factory.begin() as session:
        target_plan(session, plan_snapshot(value), apply=True)
        item = session.scalar(select(ReferenceItem).where(
            ReferenceItem.source_database == "okrug", ReferenceItem.source_entity == "dbo.ref_executors"))
        identity = item.id
        item.item_metadata = {**item.item_metadata, "manual_attribute": {"keep": True}}
    for key in ("position", "department"):
        if clear_mode == "null":
            raw[key] = None
        else:
            raw.pop(key)
    with factory.begin() as session:
        result = target_plan(session, plan_snapshot(value), apply=True)
        assert result["updated"] == 1
        item = session.get(ReferenceItem, identity)
        assert "position" not in item.item_metadata
        assert "department" not in item.item_metadata
        assert item.item_metadata["manual_attribute"] == {"keep": True}
        assert item.item_metadata["source_attributes"] == raw
    with factory.begin() as session:
        assert target_plan(session, plan_snapshot(value), apply=True)["unchanged"] == 20


def test_import_emits_changed_set_event_and_worker_refreshes_searchable_reference_label(factory):
    from uuid import UUID

    from migration.reference_import import target_plan
    from sqlalchemy import func, select

    from app.content.indexing import index_metadata
    from app.content.models import DocumentIndexState, MetadataFragment
    from app.content.worker import process_backlog
    from app.jobs.worker import process_batch
    from app.platform.models import AuditEvent, OutboxEvent
    from app.registry.models import Document, ReferenceItem, Registry
    from tests.test_content_security import actors_and_document

    sample = actors_and_document(factory, [{"key": "category", "label": "Synthetic classification",
        "type": "reference", "reference_code": "status", "searchable": True}])
    value = snapshot()
    value["sources"][0]["registry_id"] = str(sample["registry"])
    value["sources"][0]["tables"]["dbo.ref_status"].append({"id": 2, "name": "Synthetic second classification"})
    with factory.begin() as session:
        source = value["sources"][1]
        session.add(Registry(id=UUID(source["registry_id"]), code=source["database"],
                             name="Synthetic registry", document_type_ids=[]))
    with factory.begin() as session:
        target_plan(session, plan_snapshot(value), apply=True)
        item = session.scalar(select(ReferenceItem).where(
            ReferenceItem.source_database == "okrug", ReferenceItem.source_entity == "dbo.ref_status",
            ReferenceItem.source_id == "1"))
        book_id = item.reference_set_id
        document = session.get(Document, sample["document"])
        document.custom_data = {"category": str(item.id)}
        index_metadata(session, document.id)
    assert process_batch(factory, 100) == 20
    process_backlog(factory, 5)
    with factory.begin() as session:
        old = session.scalar(select(MetadataFragment.text).where(
            MetadataFragment.document_id == sample["document"], MetadataFragment.field_key == "category"))
        assert old == "Synthetic"
        count_before = session.scalar(select(func.count()).select_from(OutboxEvent))
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 20
    value["sources"][0]["tables"]["dbo.ref_status"][0]["name"] = "Synthetic revised classification"
    changed_plan = plan_snapshot(value)
    with factory.begin() as session:
        assert target_plan(session, changed_plan)["updated"] == 1
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == count_before
        assert target_plan(session, changed_plan, apply=True)["updated"] == 1
        event = session.scalar(select(OutboxEvent).where(OutboxEvent.state == "pending"))
        assert event.event_type == "reference.imported"
        assert event.object_id == book_id
        assert event.payload == {"registry_id": str(sample["registry"])}
        audits = list(session.scalars(select(AuditEvent)))
        assert len(audits) == 21
        assert all(audit.actor_id is None and audit.action == "reference.imported" for audit in audits)
        assert all(set(audit.details) == {"registry_id"} for audit in audits)
    assert process_batch(factory, 100) == 1
    with factory.begin() as session:
        assert session.get(DocumentIndexState, sample["document"]).index_version == 0
    process_backlog(factory, 5)
    with factory.begin() as session:
        refreshed = session.scalar(select(MetadataFragment.text).where(
            MetadataFragment.document_id == sample["document"], MetadataFragment.field_key == "category"))
        assert refreshed == "Synthetic revised classification"
        count_after = session.scalar(select(func.count()).select_from(OutboxEvent))
        assert count_after == count_before + 1
        assert target_plan(session, changed_plan, apply=True)["unchanged"] == 21
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == count_after
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 21


def test_standalone_cli_applies_with_fresh_model_registry_and_repeats_without_events(factory, tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path
    from uuid import UUID

    from sqlalchemy import func, select

    from app.platform.models import AuditEvent, OutboxEvent
    from app.registry.models import ReferenceItem, Registry

    value = snapshot()
    with factory.begin() as session:
        for source in value["sources"]:
            session.add(Registry(id=UUID(source["registry_id"]), code=source["database"],
                                 name="Synthetic registry", document_type_ids=[]))
    snapshot_file = tmp_path / "synthetic-reference-snapshot.json"
    snapshot_file.write_text(json.dumps(value), encoding="utf-8")
    script = Path(__file__).resolve().parents[2] / "migration" / "reference_import.py"
    report_file = tmp_path / "synthetic-reference-report.json"
    command = [sys.executable, str(script)]
    preview = subprocess.run(command + ["plan", str(snapshot_file), "--report", str(report_file)],
                             cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert preview.returncode == 0, preview.stderr
    planned = json.loads(report_file.read_text(encoding="utf-8"))
    assert planned["created"] == 20
    arguments = ["apply", str(snapshot_file), "--report", str(report_file),
                 "--expected-fingerprint", planned["fingerprint"]]
    applied = subprocess.run(command + arguments, cwd=tmp_path, capture_output=True,
                             text=True, timeout=30)
    assert applied.returncode == 0, applied.stderr
    result = json.loads(report_file.read_text(encoding="utf-8"))
    assert result["created"] == 20 and result["unchanged"] == 0
    with factory.begin() as session:
        assert session.scalar(select(func.count()).select_from(ReferenceItem)) == 20
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 20
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 20
    repeated = subprocess.run(command + arguments, cwd=tmp_path, capture_output=True,
                              text=True, timeout=30)
    assert repeated.returncode == 0, repeated.stderr
    result = json.loads(report_file.read_text(encoding="utf-8"))
    assert result["created"] == 0 and result["unchanged"] == 20
    with factory.begin() as session:
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 20
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 20
