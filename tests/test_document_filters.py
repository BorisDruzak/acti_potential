import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from database_optimized import DatabaseManager


def _create_manager(tmp_path):
    return DatabaseManager(str(tmp_path / "database.db"), create_if_not_exists=True)


def _seed_documents(manager):
    rows = manager.execute_query(
        "SELECT id FROM ref_status WHERE name = ?",
        ("Действует",),
    )
    status_id = rows[0]["id"] if rows else None

    for document in (
        {
            "title": "Alpha",
            "reg_number": "ABC-123",
            "number": "10",
            "reg_date": "2026-04-20",
            "status_id": status_id,
        },
        {
            "title": "Beta",
            "reg_number": "XYZ-999",
            "number": "20",
            "reg_date": "21.04.2026",
            "status_id": status_id,
        },
        {
            "title": "Gamma",
            "reg_number": "ABC-777",
            "number": "30",
            "reg_date": "2026-03-10",
            "status_id": status_id,
        },
    ):
        manager.add_document(document)


def test_paginated_filters_support_numbers_and_dates(tmp_path):
    previous_encoding = os.environ.get("PYTHONIOENCODING")
    os.environ["PYTHONIOENCODING"] = "utf-8"

    manager = _create_manager(tmp_path)
    try:
        _seed_documents(manager)

        cases = (
            ({"reg_number": "ABC"}, {"ABC-123", "ABC-777"}),
            ({"number": "20"}, {"XYZ-999"}),
            ({"date_exact": "2026-04-20"}, {"ABC-123"}),
            ({"date_exact": "2026-04-21"}, {"XYZ-999"}),
            ({"date_from": "2026-04-01", "date_to": "2026-04-30"}, {"ABC-123", "XYZ-999"}),
        )

        for filters, expected_numbers in cases:
            documents, total_count, has_more = manager.get_documents_paginated(
                filters=filters,
                per_page=100,
            )

            assert {item["reg_number"] for item in documents} == expected_numbers
            assert total_count == len(expected_numbers)
            assert has_more is False
    finally:
        manager.close()
        if previous_encoding is None:
            os.environ.pop("PYTHONIOENCODING", None)
        else:
            os.environ["PYTHONIOENCODING"] = previous_encoding


def test_compact_filters_match_paginated_reg_number_filter(tmp_path):
    manager = _create_manager(tmp_path)
    try:
        _seed_documents(manager)

        compact_documents = manager.get_compact_documents(
            filters={"reg_number": "ABC"},
            limit=100,
        )

        assert {item["reg_number"] for item in compact_documents} == {"ABC-123", "ABC-777"}
    finally:
        manager.close()
