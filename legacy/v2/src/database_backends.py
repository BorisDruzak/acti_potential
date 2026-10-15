import json
import os
import io
from contextlib import redirect_stdout
from datetime import datetime
from typing import Any, Dict, List, Optional

import app_config
from database_optimized import DatabaseManager as SQLiteDatabaseManager
from database_sqlserver import SqlServerDatabaseManager


class DatabaseSwitcher:
    """Backend-aware profile switcher for SQLite and SQL Server."""

    def __init__(self, config_file: str = "db_config.json"):
        self.app_root = app_config.get_app_root()
        self.config_file = config_file if os.path.isabs(config_file) else os.path.join(self.app_root, config_file)
        config_data = self.load_config()
        self.databases = config_data.get("databases", {})
        self.current_db: Optional[str] = None
        self.current_manager = None

        requested_profile = (os.getenv("ACTI_DB_PROFILE") or "").strip()
        initial_profile = requested_profile or config_data.get("last_used")
        self._auto_connect(initial_profile)

    def _set_current_manager(self, profile_name: str, manager) -> None:
        self.current_manager = manager
        self.current_db = profile_name

    def _connect_profile(self, profile_name: str):
        manager = self._create_manager(profile_name, self.databases[profile_name])
        self._set_current_manager(profile_name, manager)
        return manager

    def _auto_connect(self, preferred_profile: Optional[str] = None) -> None:
        attempted: List[str] = []

        if preferred_profile and preferred_profile in self.databases:
            attempted.append(preferred_profile)
            try:
                self._connect_profile(preferred_profile)
                print(f"[db-switcher] auto-connected profile: {preferred_profile}")
                return
            except Exception as exc:
                print(f"[db-switcher] failed to connect initial profile '{preferred_profile}': {exc}")

        for profile_name in self.databases.keys():
            if profile_name in attempted:
                continue
            try:
                self._connect_profile(profile_name)
                print(f"[db-switcher] auto-connected fallback profile: {profile_name}")
                if profile_name != preferred_profile:
                    self.save_config()
                return
            except Exception as exc:
                print(f"[db-switcher] fallback profile '{profile_name}' is unavailable: {exc}")

    def _path_to_relative(self, absolute_path: str) -> str:
        abs_path = os.path.abspath(absolute_path)
        abs_root = os.path.abspath(self.app_root)
        if os.path.normcase(abs_path).startswith(os.path.normcase(abs_root) + os.sep):
            return os.path.relpath(abs_path, abs_root)
        return absolute_path

    def _path_to_absolute(self, relative_path: str) -> str:
        if os.path.isabs(relative_path):
            return relative_path
        return os.path.abspath(os.path.join(self.app_root, relative_path))

    def _resolve_database_path(self, profile_name: str, raw_path: str) -> str:
        normalized = self._path_to_absolute(raw_path)
        if os.path.exists(normalized):
            return normalized

        path_str = str(raw_path)
        path_lower = path_str.replace("/", "\\").lower()
        legacy_marker = "\\src\\data\\"
        marker_index = path_lower.find(legacy_marker)
        if marker_index != -1:
            suffix = path_str[marker_index + len(legacy_marker) :].lstrip("\\/")
            for candidate in (
                os.path.abspath(os.path.join(self.app_root, "data", suffix)),
                os.path.abspath(os.path.join(self.app_root, "src", "data", suffix)),
            ):
                if os.path.exists(candidate):
                    return candidate

        for fallback in (
            os.path.abspath(os.path.join(self.app_root, "data", profile_name, "database.db")),
            os.path.abspath(os.path.join(self.app_root, "src", "data", profile_name, "database.db")),
        ):
            if os.path.exists(fallback):
                return fallback
        return normalized

    def _normalize_profile(self, name: str, raw_profile: Dict[str, Any]) -> Dict[str, Any]:
        profile = dict(raw_profile or {})
        backend = str(profile.get("backend") or "sqlite").strip().lower()
        profile["backend"] = backend
        if backend == "sqlite":
            if "path" in profile:
                profile["path"] = self._resolve_database_path(name, profile["path"])
        else:
            if "port" in profile and profile["port"] not in (None, ""):
                profile["port"] = int(profile["port"])
            profile.setdefault("encrypt", True)
            profile.setdefault("trust_server_certificate", True)
        return profile

    def load_config(self) -> Dict[str, Any]:
        if not os.path.exists(self.config_file):
            return {"databases": {}, "last_used": None}

        try:
            with open(self.config_file, "r", encoding="utf-8") as handle:
                config_data = json.load(handle)
        except Exception as exc:
            print(f"[db-switcher] failed to load config: {exc}")
            return {"databases": {}, "last_used": None}

        if isinstance(config_data, dict) and "databases" in config_data:
            databases = config_data.get("databases", {})
            normalized = {name: self._normalize_profile(name, info) for name, info in databases.items()}
            return {"databases": normalized, "last_used": config_data.get("last_used")}

        if isinstance(config_data, dict):
            normalized = {name: self._normalize_profile(name, info) for name, info in config_data.items()}
            return {"databases": normalized, "last_used": None}

        return {"databases": {}, "last_used": None}

    def save_config(self):
        databases_to_save: Dict[str, Dict[str, Any]] = {}
        for profile_name, profile in self.databases.items():
            profile_copy = dict(profile)
            profile_copy.pop("name", None)
            if profile_copy.get("backend", "sqlite") == "sqlite" and "path" in profile_copy:
                profile_copy["path"] = self._path_to_relative(profile_copy["path"])
            databases_to_save[profile_name] = profile_copy

        payload = {"databases": databases_to_save, "last_used": self.current_db}
        os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
        with open(self.config_file, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def _create_manager(self, profile_name: str, profile: Dict[str, Any], create_if_not_exists: bool = False):
        backend = profile.get("backend", "sqlite")
        if backend == "sqlserver":
            config = dict(profile)
            config["profile_name"] = profile_name
            return SqlServerDatabaseManager(config, create_if_not_exists=create_if_not_exists)

        db_path = profile.get("path")
        if not db_path:
            raise ValueError(f"SQLite profile '{profile_name}' does not define a path")
        with redirect_stdout(io.StringIO()):
            return SQLiteDatabaseManager(db_path, create_if_not_exists=create_if_not_exists)

    def get_profile(self, name: str) -> Dict[str, Any]:
        if name not in self.databases:
            raise ValueError(f"Database profile '{name}' not found")
        return dict(self.databases[name])

    def add_database(self, name: str, path: str):
        self.databases[name] = self._normalize_profile(
            name,
            {"backend": "sqlite", "path": path, "added": datetime.now().isoformat()},
        )
        self.save_config()

    def add_sqlserver_profile(
        self,
        name: str,
        server: str,
        database: str,
        user: str,
        *,
        port: int = 1433,
        files_root: str = "",
        storage_subdir: str = "",
        password: str = "",
        password_env: str = "ACTI_SQLSERVER_PASSWORD",
        encrypt: bool = True,
        trust_server_certificate: bool = True,
    ):
        profile_payload = {
            "backend": "sqlserver",
            "server": server,
            "port": port,
            "database": database,
            "user": user,
            "files_root": files_root,
            "storage_subdir": storage_subdir,
            "encrypt": encrypt,
            "trust_server_certificate": trust_server_certificate,
            "added": datetime.now().isoformat(),
        }
        if password:
            profile_payload["password"] = password
        else:
            profile_payload["password_env"] = password_env

        self.databases[name] = self._normalize_profile(
            name,
            profile_payload,
        )
        self.save_config()

    def switch_database(self, name: str):
        profile = self.get_profile(name)
        new_manager = self._create_manager(name, profile)
        if self.current_manager:
            try:
                self.current_manager.close()
            except Exception as exc:
                print(f"[db-switcher] warning while closing previous backend: {exc}")
        self.current_manager = new_manager
        self.current_db = name
        self.save_config()
        return new_manager

    def has_profiles(self) -> bool:
        return bool(self.databases)

    def ensure_default_sqlite_database(self, name: str, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with redirect_stdout(io.StringIO()):
            manager = SQLiteDatabaseManager(path, create_if_not_exists=not os.path.exists(path))
        self.register_database(name, path, manager)
        return manager

    def create_new_database(self, name: str, path: str):
        with redirect_stdout(io.StringIO()):
            manager = SQLiteDatabaseManager(path, create_if_not_exists=True)
        self.register_database(name, path, manager)
        return manager

    def register_database(self, name: str, path: str, manager):
        self.databases[name] = self._normalize_profile(
            name,
            {"backend": "sqlite", "path": path, "added": datetime.now().isoformat()},
        )
        self.current_db = name
        self.current_manager = manager
        self.save_config()

    def get_database_list(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for name, profile in self.databases.items():
            item = {"name": name, **profile}
            if "path" not in item:
                server = profile.get("server", "localhost")
                port = profile.get("port", 1433)
                database = profile.get("database", "")
                item["path"] = f"sqlserver://{server}:{port}/{database}"
            items.append(item)
        return items

    def validate_database_schema(self, db_path: str) -> tuple[bool, str]:
        import sqlite3

        try:
            connection = sqlite3.connect(db_path, timeout=5.0)
            cursor = connection.cursor()
            required_tables = [
                "documents",
                "ref_status",
                "ref_document_types",
                "ref_signing_types",
                "ref_document_kinds",
                "ref_themes",
                "ref_executors",
                "ref_responsible_executors",
                "ref_published_where",
                "ref_signers",
                "ref_approvers",
                "document_signers",
                "document_approvers",
            ]
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            existing_tables = [row[0] for row in cursor.fetchall()]
            missing_tables = [table for table in required_tables if table not in existing_tables]
            if missing_tables:
                connection.close()
                return False, f"Missing tables: {', '.join(missing_tables)}"

            cursor.execute("PRAGMA table_info(documents)")
            columns = [row[1] for row in cursor.fetchall()]
            required_columns = [
                "id",
                "reg_number",
                "reg_date",
                "title",
                "status_id",
                "type_id",
                "executor_id",
                "theme_id",
                "document_path",
            ]
            missing_columns = [column for column in required_columns if column not in columns]
            if missing_columns:
                connection.close()
                return False, f"Missing document columns: {', '.join(missing_columns)}"

            cursor.execute("SELECT COUNT(*) FROM ref_status")
            status_count = cursor.fetchone()[0]
            connection.close()
            if status_count == 0:
                return False, "Database is empty (reference data missing)"
            return True, "SQLite schema is valid"
        except sqlite3.Error as exc:
            return False, f"SQLite error: {exc}"
        except Exception as exc:
            return False, f"Validation error: {exc}"
