import os
import uuid
import sys
import json
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from database_backends import DatabaseSwitcher


PASSWORD_ENV = "ACTI_SQLSERVER_PASSWORD"
PROFILE_NAME = os.getenv("ACTI_TEST_PROFILE", "sqlserver_test")


@pytest.fixture(scope="module")
def sqlserver_manager():
    password = os.getenv(PASSWORD_ENV, "").strip()
    if not password:
        pytest.skip(f"{PASSWORD_ENV} is not set")

    switcher = DatabaseSwitcher("db_config.json")
    if PROFILE_NAME not in switcher.databases:
        pytest.skip(f"Profile '{PROFILE_NAME}' is not configured")

    manager = switcher._create_manager(PROFILE_NAME, switcher.get_profile(PROFILE_NAME), create_if_not_exists=True)
    yield manager
    manager.close()


def test_sqlserver_connection(sqlserver_manager):
    rows = sqlserver_manager.execute_query("SELECT DB_NAME() AS database_name")
    assert rows
    assert rows[0]["database_name"]


def test_sqlserver_document_crud(sqlserver_manager):
    unique_suffix = uuid.uuid4().hex[:8]
    title = f"__codex_test__{unique_suffix}"
    document_data = {
        "title": title,
        "reg_number": f"TEST-{unique_suffix}",
        "reg_date": "2026-03-12",
        "number": "1",
        "document_path": f"files/tests/{title}.txt",
        "should_publish": "Нет",
        "removed_from_control": "",
        "execution_result": "",
        "signers": [],
        "approvers": [],
    }

    document_id = sqlserver_manager.add_document(document_data)
    loaded = sqlserver_manager.get_document_by_id(document_id)
    assert loaded["title"] == title

    snapshot = sqlserver_manager.get_document_edit_snapshot(document_id)
    sqlserver_manager.update_document(document_id, {"title": f"{title}-updated"}, expected_snapshot=snapshot)
    loaded_after_update = sqlserver_manager.get_document_by_id(document_id)
    assert loaded_after_update["title"] == f"{title}-updated"

    sqlserver_manager.delete_document(document_id)
    assert sqlserver_manager.get_document_by_id(document_id) == {}


def test_sqlserver_helper_methods(sqlserver_manager):
    unique_suffix = uuid.uuid4().hex[:8]
    title = f"__helper_test__{unique_suffix}"
    document_data = {
        "title": title,
        "reg_number": f"HELPER-{unique_suffix}",
        "reg_date": "2026-03-12",
        "number": "1",
        "document_path": f"files/tests/{title}.txt",
        "should_publish": "Нет",
        "removed_from_control": "",
        "execution_result": "",
        "signers": [],
        "approvers": [],
    }

    document_id = sqlserver_manager.add_document(document_data)
    try:
        compact_rows = sqlserver_manager.get_compact_documents(
            filters={"search_text": title},
            limit=10,
        )
        assert any(row["id"] == document_id for row in compact_rows)

        period_rows = sqlserver_manager.get_documents_for_period("2026-03-12", "2026-03-12")
        assert any(row["id"] == document_id for row in period_rows)

        status_id = sqlserver_manager.get_reference_id_by_name("ref_status", "В работе")
        assert status_id is None or isinstance(status_id, int)
    finally:
        sqlserver_manager.delete_document(document_id)


def test_database_switcher_can_add_sqlserver_profile_with_local_password():
    password = os.getenv(PASSWORD_ENV, "").strip()
    if not password:
        pytest.skip(f"{PASSWORD_ENV} is not set")

    source_config = Path(__file__).resolve().parents[1] / "db_config.json"
    with source_config.open("r", encoding="utf-8") as handle:
        config_data = json.load(handle)

    with tempfile.TemporaryDirectory(prefix="acti_profile_test_") as temp_dir:
        temp_config = Path(temp_dir) / "db_config.json"
        temp_config.write_text(json.dumps(config_data, ensure_ascii=False, indent=2), encoding="utf-8")

        switcher = DatabaseSwitcher(str(temp_config))
        profile_name = f"gui_sqlserver_profile_{uuid.uuid4().hex[:8]}"
        switcher.add_sqlserver_profile(
            name=profile_name,
            server="192.168.100.11",
            database="acti_v2",
            user="acti_user",
            password=password,
            files_root=temp_dir,
            storage_subdir="okrug",
            port=1433,
            encrypt=True,
            trust_server_certificate=True,
        )

        saved_profile = switcher.get_profile(profile_name)
        assert saved_profile["backend"] == "sqlserver"
        assert saved_profile["password"] == password
        assert saved_profile["storage_subdir"] == "okrug"

        manager = switcher.switch_database(profile_name)
        try:
            assert getattr(manager, "backend", None) == "sqlserver"
            assert str(manager.files_root).endswith("okrug")
            rows = manager.execute_query("SELECT DB_NAME() AS database_name")
            assert rows
            assert rows[0]["database_name"]
        finally:
            manager.close()
