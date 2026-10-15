import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Optional

import app_config


class DatabaseLockError(Exception):
    """Database write lock or transient busy condition."""


class DocumentConflictError(Exception):
    """Conflict while editing a document concurrently."""

    def __init__(self, message: str, current_snapshot: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.current_snapshot = current_snapshot or {}


def rows_to_dicts(cursor, rows: Iterable[Any]) -> List[Dict[str, Any]]:
    """Convert DB-API rows to dictionaries using cursor metadata."""
    columns = [column[0] for column in (cursor.description or [])]
    result: List[Dict[str, Any]] = []
    for row in rows:
        if isinstance(row, Mapping):
            result.append(dict(row))
            continue
        result.append({columns[index]: value for index, value in enumerate(row)})
    return result


def row_to_dict(cursor, row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    rows = rows_to_dicts(cursor, [row])
    return rows[0] if rows else {}


class FileStorageMixin:
    """Shared file storage helpers for SQLite and SQL Server backends."""

    app_root: str
    profile_name: str
    db_folder: str
    files_root: Path
    files_base_path: str
    local_files_root: Path

    @staticmethod
    def sanitize_profile_name(name: str) -> str:
        raw = (name or "default").strip()
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw) or "default"

    @staticmethod
    def normalize_document_path(relative_path: str) -> str:
        rel = str(PurePosixPath((relative_path or "").replace("\\", "/")))
        if rel.startswith("files/"):
            return rel
        return f"files/{rel}" if rel else ""

    def setup_file_storage(
        self,
        profile_name: str,
        files_root: Optional[str] = None,
        local_root: Optional[str] = None,
        storage_subdir: Optional[str] = None,
        allow_local_fallback: bool = True,
    ) -> None:
        self.app_root = getattr(self, "app_root", app_config.get_app_root())
        self.profile_name = self.sanitize_profile_name(profile_name)
        self.storage_subdir = (
            self.sanitize_profile_name(storage_subdir) if str(storage_subdir or "").strip() else ""
        )

        self.db_folder = str(Path(self.app_root) / "data" / self.profile_name)
        Path(self.db_folder).mkdir(parents=True, exist_ok=True)

        self.local_files_root = Path(local_root) if local_root else Path(self.db_folder) / "files"
        self.local_files_root.mkdir(parents=True, exist_ok=True)

        env_specific = os.getenv(f"ACTI_FILES_ROOT_{self.profile_name.upper()}", "").strip()
        env_default = os.getenv("ACTI_FILES_ROOT", "").strip()
        configured_root = (files_root or "").strip()

        selected_root = configured_root or env_specific or env_default
        if selected_root:
            candidate = Path(selected_root)
            if self.storage_subdir:
                candidate = candidate / self.storage_subdir
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                self.files_root = candidate
            except OSError as exc:
                if allow_local_fallback:
                    print(f"[files-root] unavailable: {candidate}. Falling back to local files root.")
                    self.files_root = self.local_files_root
                else:
                    raise FileNotFoundError(
                        f"Configured files storage is unavailable: {candidate}"
                    ) from exc
        else:
            if allow_local_fallback:
                self.files_root = self.local_files_root
            else:
                raise ValueError("SQL Server profile requires a configured files_root.")

        self.files_base_path = str(self.files_root)
        if self.files_root == self.local_files_root:
            self.local_files_root.mkdir(parents=True, exist_ok=True)

        print(f"[files-root] active root: {self.files_root}")

    def get_files_path(self, year: int, month: int) -> str:
        year_folder = Path(self.files_root) / str(year)
        month_folder = year_folder / str(month)
        month_folder.mkdir(parents=True, exist_ok=True)
        return str(month_folder)

    def get_full_file_path(self, relative_path: str) -> str:
        if not relative_path:
            raise ValueError("Empty document path")

        if os.path.isabs(relative_path):
            return relative_path

        rel = str(PurePosixPath(relative_path.replace("\\", "/")))
        path = PurePosixPath(rel)
        if ".." in path.parts or rel.startswith(("/", "\\")) or ":" in rel:
            raise ValueError(f"Invalid document path in database: {relative_path}")

        if rel.startswith("files/"):
            rel = rel[len("files/") :]

        full = Path(self.files_root) / Path(*PurePosixPath(rel).parts)
        return str(full)

    def make_relative_document_path(self, absolute_path: str) -> str:
        full_path = Path(absolute_path).resolve()

        try:
            relative = full_path.relative_to(Path(self.files_root).resolve())
        except Exception:
            try:
                relative = full_path.relative_to(Path(self.db_folder).resolve())
            except Exception as exc:
                raise ValueError(
                    f"File {absolute_path} is outside the configured storage root {self.files_root}"
                ) from exc

        return self.normalize_document_path(str(relative).replace("\\", "/"))
