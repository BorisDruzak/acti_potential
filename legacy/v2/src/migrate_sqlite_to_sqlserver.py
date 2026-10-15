import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable

from database_backends import DatabaseSwitcher


REFERENCE_TABLES = (
    "ref_status",
    "ref_document_types",
    "ref_signing_types",
    "ref_document_kinds",
    "ref_published_where",
    "ref_executors",
    "ref_responsible_executors",
    "ref_themes",
    "ref_signers",
    "ref_approvers",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate ACTI SQLite data into SQL Server profile.")
    parser.add_argument("--sqlite-path", required=True, help="Path to source SQLite database.")
    parser.add_argument("--profile", default="sqlserver_test", help="Target profile name from db_config.json.")
    parser.add_argument("--config", default="db_config.json", help="Path to db_config.json.")
    parser.add_argument("--report", default="migration_report.json", help="Where to save migration report.")
    parser.add_argument("--truncate", action="store_true", help="Delete target documents before import.")
    return parser.parse_args()


def fetch_table(connection: sqlite3.Connection, table_name: str):
    cursor = connection.cursor()
    cursor.execute(f"SELECT * FROM {table_name}")
    return [dict(row) for row in cursor.fetchall()]


def insert_reference_row(manager, table_name: str, payload: Dict) -> int:
    columns = list(payload.keys())
    placeholders = ", ".join("?" for _ in columns)
    with manager.transaction() as cursor:
        cursor.raw_cursor.execute(
            f"INSERT INTO {table_name} ({', '.join(columns)}) OUTPUT INSERTED.id VALUES ({placeholders})",
            tuple(payload[column] for column in columns),
        )
        row = cursor.raw_cursor.fetchone()
    return int(row[0])


def normalize_date_value(value):
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(text).strftime("%Y-%m-%d")
    except ValueError:
        return text


def main() -> int:
    args = parse_args()
    sqlite_path = Path(args.sqlite_path)
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {sqlite_path}")

    switcher = DatabaseSwitcher(args.config)
    profile = switcher.get_profile(args.profile)
    manager = switcher._create_manager(args.profile, profile, create_if_not_exists=True)

    source = sqlite3.connect(str(sqlite_path))
    source.row_factory = sqlite3.Row

    report = {
        "profile": args.profile,
        "sqlite_path": str(sqlite_path),
        "reference_counts": {},
        "document_count": 0,
        "validation": {},
    }

    if args.truncate:
        manager.execute_update("DELETE FROM document_signers")
        manager.execute_update("DELETE FROM document_approvers")
        manager.execute_update("DELETE FROM documents")
        for table_name in REFERENCE_TABLES:
            manager.execute_update(f"DELETE FROM {table_name}")

    target_reference_id_maps: Dict[str, Dict[int, int]] = {}

    for table_name in REFERENCE_TABLES:
        source_rows = fetch_table(source, table_name)
        report["reference_counts"][table_name] = len(source_rows)
        target_reference_id_maps[table_name] = {}
        for row in source_rows:
            source_id = int(row["id"])
            existing = manager.execute_query(f"SELECT id FROM {table_name} WHERE name = ?", (row["name"],))
            if existing:
                target_reference_id_maps[table_name][source_id] = int(existing[0]["id"])
                continue

            payload = dict(row)
            payload.pop("id", None)
            target_reference_id_maps[table_name][source_id] = insert_reference_row(manager, table_name, payload)

    source_documents = fetch_table(source, "documents")
    for source_document in source_documents:
        document_id = int(source_document.pop("id"))
        payload = dict(source_document)

        ref_mapping = {
            "status_id": "ref_status",
            "type_id": "ref_document_types",
            "signing_type_id": "ref_signing_types",
            "document_kind_id": "ref_document_kinds",
            "published_where_id": "ref_published_where",
            "executor_id": "ref_executors",
            "responsible_executor_id": "ref_responsible_executors",
            "theme_id": "ref_themes",
        }

        for foreign_key, table_name in ref_mapping.items():
            source_fk = payload.get(foreign_key)
            if not source_fk:
                payload[foreign_key] = None
                continue
            payload[foreign_key] = target_reference_id_maps[table_name].get(int(source_fk))

        for date_field in ("reg_date", "published_date", "control_date"):
            payload[date_field] = normalize_date_value(payload.get(date_field))

        signer_ids = []
        for row in source.execute("SELECT signer_id FROM document_signers WHERE document_id = ?", (document_id,)):
            target_id = target_reference_id_maps["ref_signers"].get(int(row["signer_id"]))
            if target_id:
                signer_ids.append(target_id)

        approver_ids = []
        for row in source.execute("SELECT approver_id FROM document_approvers WHERE document_id = ?", (document_id,)):
            target_id = target_reference_id_maps["ref_approvers"].get(int(row["approver_id"]))
            if target_id:
                approver_ids.append(target_id)

        payload["signers"] = signer_ids
        payload["approvers"] = approver_ids
        manager.add_document(payload)

    report["document_count"] = len(source_documents)
    report["validation"] = {
        "target_documents": manager.get_documents_count(),
        "target_signers_relations": manager.execute_query("SELECT COUNT(*) AS total_count FROM document_signers")[0]["total_count"],
        "target_approvers_relations": manager.execute_query("SELECT COUNT(*) AS total_count FROM document_approvers")[0]["total_count"],
    }

    with open(args.report, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
