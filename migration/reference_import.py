"""Source-qualified reference snapshot importer. Never connects to SQL Server.

The input is private JSON, version 1, with both sources (okrug and raion), each
containing a registry_id and all ten dbo.ref_* lists of complete source rows.
Optional source.adopt maps table -> source ID -> reviewed target item UUID.
Only explicit, source-verified fixture/default adoptions may use that mapping.
"""

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

TABLES = {
    "dbo.ref_status": ("status", "Статусы"),
    "dbo.ref_document_types": ("document_types", "Типы документов"),
    "dbo.ref_signing_types": ("signing_types", "Типы подписания"),
    "dbo.ref_document_kinds": ("document_kinds", "Виды документов"),
    "dbo.ref_published_where": ("published_where", "Места публикации"),
    "dbo.ref_executors": ("executors", "Исполнители"),
    "dbo.ref_responsible_executors": ("responsible_executors", "Ответственные исполнители"),
    "dbo.ref_themes": ("themes", "Темы"),
    "dbo.ref_signers": ("signers", "Подписанты"),
    "dbo.ref_approvers": ("approvers", "Согласующие"),
}
PEOPLE_CODES = {"executors", "responsible_executors", "signers", "approvers"}


class SnapshotError(ValueError):
    """Aggregate validation codes/counts only: never include private source values."""

    def __init__(self, errors):
        self.errors = dict(sorted(errors.items()))
        super().__init__(json.dumps(self.errors, sort_keys=True))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def stable_id(*parts):
    return str(uuid5(NAMESPACE_URL, "acti:reference-import:v1:" + canonical(parts)))


def missing_label(database, table, source_id):
    """Represent an empty source name without changing its preserved raw value."""
    entity = table.removeprefix("dbo.")
    label = f"Без наименования [{database}/{entity}/{source_id}]"
    if len(label) > 255:
        label = f"Без наименования [{database}/{entity}/{stable_id(database, table, source_id)}]"
    return label


def plan_snapshot(snapshot):
    errors = Counter()
    items, sets, databases, registries = [], [], set(), set()
    if not isinstance(snapshot, dict) or snapshot.get("version") != 1:
        raise SnapshotError({"invalid_version": 1})
    sources = snapshot.get("sources")
    if not isinstance(sources, list):
        raise SnapshotError({"invalid_sources": 1})
    for source in sources:
        if not isinstance(source, dict):
            errors["invalid_source"] += 1
            continue
        database = source.get("database")
        if database not in {"okrug", "raion"} or database in databases:
            errors["invalid_database"] += 1
            continue
        databases.add(database)
        try:
            registry_id = str(UUID(source["registry_id"]))
        except (KeyError, ValueError, TypeError, AttributeError):
            errors["invalid_registry"] += 1
            continue
        if registry_id in registries:
            errors["shared_registry"] += 1
        registries.add(registry_id)
        tables = source.get("tables", {})
        if not isinstance(tables, dict):
            errors["invalid_tables"] += 1
            continue
        errors["missing_tables"] += len(set(TABLES) - set(tables))
        errors["unknown_tables"] += len(set(tables) - set(TABLES))
        adoptions = source.get("adopt", {})
        if not isinstance(adoptions, dict) or set(adoptions) - set(TABLES):
            errors["invalid_adoptions"] += 1
            adoptions = {}
        for table, (code, name) in TABLES.items():
            rows = tables.get(table)
            if rows is None:
                continue
            if not isinstance(rows, list):
                errors["invalid_rows"] += 1
                continue
            sets.append({"database": database, "table": table, "registry_id": registry_id,
                         "code": code, "name": name, "count": len(rows)})
            identities, labels = set(), set()
            adoption = adoptions.get(table, {})
            if not isinstance(adoption, dict):
                errors["invalid_adoptions"] += 1
                adoption = {}
            for raw in rows:
                if not isinstance(raw, dict):
                    errors["invalid_row"] += 1
                    continue
                before = sum(errors.values())
                source_id = raw.get("id")
                if isinstance(source_id, bool) or not isinstance(source_id, (int, str)) or not str(source_id) or len(str(source_id)) > 255:
                    errors["invalid_source_id"] += 1
                source_id = str(source_id)
                if source_id in identities:
                    errors["duplicate_source_id"] += 1
                identities.add(source_id)
                label = raw.get("name")
                missing_source_label = label == ""
                if missing_source_label:
                    label = missing_label(database, table, source_id)
                if not isinstance(label, str) or not label.strip() or len(label) > 255 or "\x00" in label:
                    errors["invalid_label"] += 1
                elif label in labels:
                    errors["duplicate_label"] += 1
                else:
                    labels.add(label)
                active = raw.get("is_active", True)
                if type(active) not in (bool, int) or active not in (0, 1):
                    errors["invalid_active"] += 1
                metadata = {"source_attributes": deepcopy(raw)}
                if missing_source_label:
                    metadata["missing_source_label"] = True
                if code in PEOPLE_CODES:
                    for key in ("position", "department"):
                        value = raw.get(key)
                        if value is not None and (not isinstance(value, str) or len(value) > 255 or "\x00" in value):
                            errors["invalid_business_metadata"] += 1
                        if value is not None:
                            metadata[key] = value
                try:
                    row_fingerprint = fingerprint(raw)
                    if "\\u0000" in canonical(raw):
                        errors["invalid_json_value"] += 1
                except (TypeError, ValueError, UnicodeError):
                    errors["invalid_json_value"] += 1
                adopted_id = adoption.get(source_id)
                if adopted_id is not None:
                    try:
                        adopted_id = str(UUID(adopted_id))
                    except (ValueError, TypeError, AttributeError):
                        errors["invalid_adoption_id"] += 1
                if sum(errors.values()) != before:
                    continue
                items.append({"id": stable_id(database, table, source_id),
                              "registry_id": registry_id, "reference_code": code,
                              "source_database": database, "source_entity": table,
                              "source_id": source_id, "label": label, "active": bool(active),
                              "metadata": metadata, "source_fingerprint": row_fingerprint,
                              "adopt_id": adopted_id})
            errors["unused_adoption"] += len(set(adoption) - identities)
    if databases != {"okrug", "raion"}:
        errors["missing_database"] += len({"okrug", "raion"} - databases)
    errors = +errors
    if errors:
        raise SnapshotError(errors)
    items.sort(key=lambda item: (item["source_database"], item["source_entity"], item["source_id"]))
    sets.sort(key=lambda item: (item["database"], item["table"]))
    body = {"sets": sets, "items": items}
    return {"fingerprint": fingerprint(body), **body}


def target_plan(session, plan, *, apply=False):
    """Validate the entire target first, then mutate atomically in caller transaction."""
    from sqlalchemy import select, text

    # Standalone CLI processes do not load the HTTP application model registry.
    # Audit and registry foreign keys must resolve users before ORM flush ordering.
    from app.identity import models as identity_models  # noqa: F401
    from app.registry.models import ReferenceItem, ReferenceSet, Registry

    session.execute(text("SELECT pg_advisory_xact_lock(1729051001)"))
    errors = Counter()
    references = {}
    new_sets = []
    counts = {"created": 0, "updated": 0, "unchanged": 0, "adopted": 0}
    prepared, selected_ids = [], set()
    for spec in plan["sets"]:
        registry_id = UUID(spec["registry_id"])
        if session.get(Registry, registry_id) is None:
            errors["missing_target_registry"] += 1
        book = session.scalar(select(ReferenceSet).where(
            ReferenceSet.registry_id == registry_id, ReferenceSet.code == spec["code"]
        ).with_for_update())
        if book is None:
            book = ReferenceSet(id=UUID(stable_id("set", str(registry_id), spec["code"])),
                                registry_id=registry_id, code=spec["code"], name=spec["name"])
            new_sets.append(book)
        references[(spec["database"], spec["table"])] = book
    for spec in plan["items"]:
        identity = (spec["source_database"], spec["source_entity"], spec["source_id"])
        book = references[identity[:2]]
        item = session.scalar(select(ReferenceItem).where(
            ReferenceItem.source_database == identity[0], ReferenceItem.source_entity == identity[1],
            ReferenceItem.source_id == identity[2]
        ).with_for_update())
        if item is not None and spec["adopt_id"] and str(item.id) != spec["adopt_id"]:
            errors["adoption_identity_changed"] += 1
        adopted = False
        if item is None and spec["adopt_id"]:
            item = session.get(ReferenceItem, UUID(spec["adopt_id"]), with_for_update=True)
            if item is None or any((item.source_database, item.source_entity, item.source_id)):
                errors["adoption_unavailable"] += 1
                continue
            adopted = True
        if item is not None and item.reference_set_id != book.id:
            errors["source_registry_mismatch"] += 1
            continue
        collision = session.scalar(select(ReferenceItem.id).where(
            ReferenceItem.reference_set_id == book.id, ReferenceItem.label == spec["label"]
        ))
        if collision and (item is None or collision != item.id):
            errors["manual_label_collision"] += 1
            continue
        creating = item is None
        if creating:
            if session.get(ReferenceItem, UUID(spec["id"])) is not None:
                errors["target_uuid_collision"] += 1
                continue
            item = ReferenceItem(id=UUID(spec["id"]), reference_set_id=book.id,
                                 code="", description="", sort_order=0, item_metadata={})
        if item.id in selected_ids:
            errors["duplicate_target_identity"] += 1
        selected_ids.add(item.id)
        previous_metadata = dict(item.item_metadata or {})
        if spec["reference_code"] in PEOPLE_CODES:
            for key in ("position", "department"):
                previous_metadata.pop(key, None)
        metadata = {**previous_metadata, **spec["metadata"]}
        if not spec["metadata"].get("missing_source_label"):
            metadata.pop("missing_source_label", None)
        changed = creating or adopted or item.source_fingerprint != spec["source_fingerprint"] or item.label != spec["label"] or item.active != spec["active"] or item.item_metadata != metadata
        kind = "adopted" if adopted else "created" if creating else "updated" if changed else "unchanged"
        counts[kind] += 1
        prepared.append((item, spec, metadata, creating, changed))
    if errors:
        raise SnapshotError(errors)
    if apply:
        from app.platform.audit import AuditInput, record_audit
        from app.platform.outbox import enqueue_event

        changed_sets = {}
        session.add_all(new_sets)
        session.flush()
        for item, spec, metadata, creating, changed in prepared:
            if not changed:
                continue
            changed_sets[item.reference_set_id] = spec["registry_id"]
            item.label, item.active, item.item_metadata = spec["label"], spec["active"], metadata
            item.source_database, item.source_entity, item.source_id = spec["source_database"], spec["source_entity"], spec["source_id"]
            item.source_fingerprint = spec["source_fingerprint"]
            if creating:
                session.add(item)
        session.flush()
        for reference_id, registry_id in sorted(changed_sets.items()):
            details = {"registry_id": registry_id}
            enqueue_event(session, "reference.imported", reference_id, details)
            record_audit(session, AuditInput(
                actor_id=None, action="reference.imported", object_type="reference",
                object_id=reference_id, details=details,
                request_id=stable_id("reference-import-run", plan["fingerprint"]),
                request_source="reference-import",
            ))
    return {"fingerprint": plan["fingerprint"], "source_count": len(plan["items"]),
            "set_count": len(plan["sets"]), "new_sets": len(new_sets), **counts,
            "sets": [{key: value for key, value in spec.items() if key != "registry_id"}
                     for spec in plan["sets"]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "apply"))
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--expected-fingerprint")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        plan = plan_snapshot(json.loads(args.snapshot.read_text(encoding="utf-8-sig")))
        if args.command == "apply" and args.expected_fingerprint != plan["fingerprint"]:
            raise SnapshotError({"fingerprint_mismatch": 1})
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        # Deliberately target-only; no source credentials, SQL driver or legacy manager.
        engine = create_engine(os.environ["ACTI_DATABASE_URL"], hide_parameters=True)
        with Session(engine) as session, session.begin():
            report = target_plan(session, plan, apply=args.command == "apply")
        engine.dispose()
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key != "sets"}))
    except SnapshotError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception:
        # DB exceptions can contain source parameters even with driver hiding enabled.
        print("Reference import failed; target transaction rolled back", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
