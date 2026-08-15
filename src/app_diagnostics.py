import io
import logging
import os
import sys
import traceback
import uuid
from collections import deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Deque, Dict, Optional

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def _coerce_message_level(message: str, default_level: int) -> int:
    upper = message.upper()
    if any(token in upper for token in ("TRACEBACK", "❌", "ОШИБКА", "CRITICAL", "EXCEPTION")):
        return logging.ERROR
    if any(token in upper for token in ("⚠", "WARNING", "WARN")):
        return logging.WARNING
    return default_level


class DiagnosticsManager(QObject):
    entry_added = pyqtSignal(dict)
    status_changed = pyqtSignal(dict)

    def __init__(self, max_entries: int = 2000):
        super().__init__()
        self.entries: Deque[Dict[str, Any]] = deque(maxlen=max_entries)
        self.alert_active = False
        self.last_error_message = ""
        self.last_error_time = ""
        self.health = {
            "ok": True,
            "checked_at": "",
            "profile": "",
            "backend": "",
            "db_path": "",
            "database_ok": None,
            "database_message": "",
            "files_root": "",
            "files_ok": None,
            "files_message": "",
        }

    def add_entry(self, level: int, logger_name: str, message: str, created: Optional[float] = None):
        timestamp = datetime.fromtimestamp(created or datetime.now().timestamp()).strftime("%Y-%m-%d %H:%M:%S")
        level_name = logging.getLevelName(level)
        text = (message or "").rstrip()
        entry = {
            "timestamp": timestamp,
            "level": level_name,
            "logger": logger_name,
            "message": text,
        }
        self.entries.append(entry)
        if level >= logging.ERROR:
            self.alert_active = True
            self.last_error_message = text
            self.last_error_time = timestamp
        self.entry_added.emit(entry)
        self.status_changed.emit(self.get_status_snapshot())

    def set_health(self, result: Dict[str, Any]):
        self.health = dict(result)
        if not result.get("ok", True):
            self.alert_active = True
            if result.get("database_message"):
                self.last_error_message = result["database_message"]
            elif result.get("files_message"):
                self.last_error_message = result["files_message"]
            self.last_error_time = result.get("checked_at", "")
        self.status_changed.emit(self.get_status_snapshot())

    def clear_alert(self):
        self.alert_active = False
        self.status_changed.emit(self.get_status_snapshot())

    def clear_entries(self):
        self.entries.clear()
        self.status_changed.emit(self.get_status_snapshot())

    def get_status_snapshot(self) -> Dict[str, Any]:
        return {
            "alert_active": self.alert_active,
            "last_error_message": self.last_error_message,
            "last_error_time": self.last_error_time,
            "health": dict(self.health),
            "entry_count": len(self.entries),
        }

    def get_formatted_entries(self) -> str:
        lines = [
            f"{item['timestamp']} [{item['level']}] {item['logger']}: {item['message']}"
            for item in self.entries
        ]
        return "\n".join(lines)


class DiagnosticsLogHandler(logging.Handler):
    def __init__(self, manager: DiagnosticsManager):
        super().__init__()
        self.manager = manager

    def emit(self, record: logging.LogRecord):
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()
        self.manager.add_entry(record.levelno, record.name, message, record.created)


class StreamToLogger(io.TextIOBase):
    def __init__(self, logger_name: str, default_level: int):
        super().__init__()
        self.logger = logging.getLogger(logger_name)
        self.default_level = default_level
        self._buffer = ""

    def write(self, text: str):
        if not text:
            return 0
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.rstrip()
            if not line:
                continue
            level = _coerce_message_level(line, self.default_level)
            self.logger.log(level, line)
        return len(text)

    def flush(self):
        line = self._buffer.rstrip()
        if line:
            level = _coerce_message_level(line, self.default_level)
            self.logger.log(level, line)
        self._buffer = ""

    def isatty(self):
        return False


_diagnostics_manager: Optional[DiagnosticsManager] = None


def get_diagnostics_manager() -> DiagnosticsManager:
    global _diagnostics_manager
    if _diagnostics_manager is None:
        _diagnostics_manager = DiagnosticsManager()
    return _diagnostics_manager


def setup_application_logging(app_root: str) -> DiagnosticsManager:
    manager = get_diagnostics_manager()
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    logs_dir = Path(app_root) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "acti.log"

    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = RotatingFileHandler(str(log_path), maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    diagnostics_handler = DiagnosticsLogHandler(manager)
    diagnostics_handler.setFormatter(formatter)
    root_logger.addHandler(diagnostics_handler)

    logging.captureWarnings(True)

    sys.stdout = StreamToLogger("acti.stdout", logging.INFO)
    sys.stderr = StreamToLogger("acti.stderr", logging.ERROR)

    def excepthook(exc_type, exc_value, exc_tb):
        logging.getLogger("acti.crash").error(
            "Необработанное исключение:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = excepthook
    logging.getLogger("acti").info("Диагностическое логирование запущено")
    return manager


def run_diagnostics_checks(db_manager, switcher) -> Dict[str, Any]:
    logger = logging.getLogger("acti.diagnostics")
    profile_name = getattr(switcher, "current_db", "") or ""
    backend = getattr(db_manager, "backend", "") or ""
    db_path = getattr(db_manager, "db_path", "") or ""
    files_root = str(getattr(db_manager, "files_root", "") or "")
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    database_ok = False
    database_message = ""
    try:
        rows = db_manager.execute_query("SELECT 1 AS ok")
        database_ok = bool(rows and int(rows[0]["ok"]) == 1)
        database_message = "Подключение к базе данных работает"
        logger.info("Диагностика БД: %s", database_message)
    except Exception as exc:
        database_message = f"Ошибка подключения к базе данных: {exc}"
        logger.error(database_message)

    files_ok = False
    files_message = ""
    try:
        root_path = Path(files_root)
        if not files_root:
            raise ValueError("Не настроен путь к файловому хранилищу")
        if not root_path.exists():
            raise FileNotFoundError(f"Папка файлов недоступна: {files_root}")

        probe_path = root_path / f".acti_diag_{uuid.uuid4().hex}.tmp"
        probe_path.write_text("diag", encoding="utf-8")
        probe_path.unlink(missing_ok=True)
        files_ok = True
        files_message = "Файловое хранилище доступно на чтение и запись"
        logger.info("Диагностика файлов: %s", files_message)
    except Exception as exc:
        files_message = f"Ошибка доступа к файловому хранилищу: {exc}"
        logger.error(files_message)

    result = {
        "ok": database_ok and files_ok,
        "checked_at": checked_at,
        "profile": profile_name,
        "backend": backend,
        "db_path": db_path,
        "database_ok": database_ok,
        "database_message": database_message,
        "files_root": files_root,
        "files_ok": files_ok,
        "files_message": files_message,
    }
    get_diagnostics_manager().set_health(result)
    return result


class DiagnosticsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = get_diagnostics_manager()
        self._db_manager = None
        self._switcher = None
        self.setWindowTitle("Диагностика")
        self.setMinimumSize(860, 520)
        self._build_ui()
        self.manager.entry_added.connect(self._append_entry)
        self.manager.status_changed.connect(self._refresh_status)
        self.log_view.setPlainText(self.manager.get_formatted_entries())
        self._refresh_status(self.manager.get_status_snapshot())

    def _build_ui(self):
        layout = QVBoxLayout(self)

        status_form = QFormLayout()
        self.profile_label = QLabel("-")
        self.backend_label = QLabel("-")
        self.db_path_label = QLabel("-")
        self.db_status_label = QLabel("-")
        self.files_root_label = QLabel("-")
        self.files_status_label = QLabel("-")
        self.checked_at_label = QLabel("-")
        self.last_error_label = QLabel("-")

        for label in (
            self.profile_label,
            self.backend_label,
            self.db_path_label,
            self.db_status_label,
            self.files_root_label,
            self.files_status_label,
            self.checked_at_label,
            self.last_error_label,
        ):
            label.setWordWrap(True)

        status_form.addRow("Профиль:", self.profile_label)
        status_form.addRow("Тип подключения:", self.backend_label)
        status_form.addRow("Подключение:", self.db_path_label)
        status_form.addRow("База данных:", self.db_status_label)
        status_form.addRow("Файловый путь:", self.files_root_label)
        status_form.addRow("Файловое хранилище:", self.files_status_label)
        status_form.addRow("Последняя проверка:", self.checked_at_label)
        status_form.addRow("Последняя ошибка:", self.last_error_label)
        layout.addLayout(status_form)

        buttons_layout = QHBoxLayout()
        self.check_button = QPushButton("Проверить сейчас")
        self.clear_alert_button = QPushButton("Сбросить тревогу")
        self.clear_logs_button = QPushButton("Очистить лог")
        buttons_layout.addWidget(self.check_button)
        buttons_layout.addWidget(self.clear_alert_button)
        buttons_layout.addWidget(self.clear_logs_button)
        buttons_layout.addStretch()
        layout.addLayout(buttons_layout)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.log_view, 1)

        self.check_button.clicked.connect(self._run_checks)
        self.clear_alert_button.clicked.connect(self.manager.clear_alert)
        self.clear_logs_button.clicked.connect(self._clear_logs)

    def set_context(self, db_manager, switcher):
        self._db_manager = db_manager
        self._switcher = switcher
        self._refresh_status(self.manager.get_status_snapshot())

    def _append_entry(self, entry: Dict[str, Any]):
        line = f"{entry['timestamp']} [{entry['level']}] {entry['logger']}: {entry['message']}"
        self.log_view.appendPlainText(line)
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _refresh_status(self, snapshot: Dict[str, Any]):
        health = snapshot.get("health", {})
        self.profile_label.setText(health.get("profile") or "-")
        self.backend_label.setText(health.get("backend") or "-")
        self.db_path_label.setText(health.get("db_path") or "-")
        self.db_status_label.setText(health.get("database_message") or "-")
        self.files_root_label.setText(health.get("files_root") or "-")
        self.files_status_label.setText(health.get("files_message") or "-")
        self.checked_at_label.setText(health.get("checked_at") or "-")
        last_error = snapshot.get("last_error_message") or "Нет"
        last_time = snapshot.get("last_error_time") or ""
        self.last_error_label.setText(f"{last_time} {last_error}".strip())

    def _run_checks(self):
        if self._db_manager is None or self._switcher is None:
            logging.getLogger("acti.diagnostics").warning("Диагностика: контекст еще не инициализирован")
            return
        run_diagnostics_checks(self._db_manager, self._switcher)

    def _clear_logs(self):
        self.manager.clear_entries()
        self.log_view.clear()
