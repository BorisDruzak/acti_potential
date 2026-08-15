import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt

from documents_table import DocumentsTableModel, DocumentsTableView
from advensed_search import SearchResultsTableModel


class _FakeDbManager:
    def __init__(self):
        self.calls = []

    def get_documents_paginated(self, page=1, per_page=100, filters=None):
        self.calls.append({"page": page, "per_page": per_page, "filters": filters})
        documents = [
            {
                "id": index,
                "title": f"Document {index}",
                "reg_number": f"N-{index:03}",
                "reg_date": "2026-04-21",
                "status_name": "Status",
                "type_name": "Type",
                "executor_name": "Executor",
                "theme_name": "Theme",
            }
            for index in range(1, min(per_page, 250) + 1)
        ]
        return documents, 250, per_page < 250


def _app():
    return QApplication.instance() or QApplication([])


class _FakeScrollBar:
    def __init__(self):
        self._value = 42

    def value(self):
        return self._value

    def maximum(self):
        return 100

    def setValue(self, value):
        self._value = value


class _FakeDocumentsTable:
    _format_documents_for_table = staticmethod(DocumentsTableView._format_documents_for_table)

    def __init__(self, db_manager):
        self.db_manager = db_manager
        self.current_page = 3
        self.per_page = 100
        self.total_documents = 250
        self.has_more = False
        self.current_filters = {"reg_number": "ABC"}
        self._all_loaded_data = [["row"] * 8 for _ in range(250)]
        self._is_loading = False
        self.scrollbar = _FakeScrollBar()
        self.model_updates = 0

    def verticalScrollBar(self):
        return self.scrollbar

    def _ensure_model(self):
        self.model_updates += 1

    def setup_column_sizes(self):
        pass

    def emit_pagination_info(self):
        pass


def test_refresh_loaded_documents_keeps_loaded_pages_and_filters():
    db_manager = _FakeDbManager()
    view = _FakeDocumentsTable(db_manager)

    DocumentsTableView.refresh_loaded_documents(view)

    assert db_manager.calls[-1] == {
        "page": 1,
        "per_page": 300,
        "filters": {"reg_number": "ABC"},
    }
    assert len(view._all_loaded_data) == 250
    assert view.current_page == 3
    assert view.has_more is False
    assert view.model_updates == 1


def test_search_results_model_sorts_reg_date_column_as_date():
    _app()
    model = SearchResultsTableModel(
        [
            [1, "Old", "A", "-", "10.03.2026"],
            [2, "New", "B", "-", "2026-04-21"],
            [3, "Mid", "C", "-", "20.03.2026"],
        ],
        ["ID", "Title", "Reg", "Number", "Date"],
    )

    model.sort(4, Qt.DescendingOrder)

    assert [row[0] for row in model._data] == [2, 3, 1]


def test_main_documents_model_sorts_reg_number_naturally():
    _app()
    model = DocumentsTableModel(
        [
            ["Doc 100", "100", "2026-01-01", "Status", "Type", "Executor", "Theme", 100],
            ["Doc 10", "10", "2026-01-01", "Status", "Type", "Executor", "Theme", 10],
            ["Doc empty", "-", "-", "Status", "Type", "Executor", "Theme", 0],
            ["Doc 2", "2", "2026-01-01", "Status", "Type", "Executor", "Theme", 2],
            ["Doc 1", "1", "2026-01-01", "Status", "Type", "Executor", "Theme", 1],
        ],
        ["Название", "№", "Дата", "Статус", "Тип", "Исполнитель", "Тема", "ID"],
    )

    model.sort(1, Qt.AscendingOrder)

    assert [row[1] for row in model._data] == ["1", "2", "10", "100", "-"]


def test_main_documents_model_keeps_sort_after_data_refresh():
    _app()
    model = DocumentsTableModel(
        [
            ["Doc 2", "2", "2026-01-01", "Status", "Type", "Executor", "Theme", 2],
            ["Doc 1", "1", "2026-01-01", "Status", "Type", "Executor", "Theme", 1],
        ],
        ["Название", "№", "Дата", "Статус", "Тип", "Исполнитель", "Тема", "ID"],
    )
    model.sort(1, Qt.AscendingOrder)

    model.update_data(
        [
            ["Doc 100", "100", "2026-01-01", "Status", "Type", "Executor", "Theme", 100],
            ["Doc 10", "10", "2026-01-01", "Status", "Type", "Executor", "Theme", 10],
            ["Doc 2", "2", "2026-01-01", "Status", "Type", "Executor", "Theme", 2],
        ]
    )

    assert [row[1] for row in model._data] == ["2", "10", "100"]
