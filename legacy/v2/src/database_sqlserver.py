import os
import re
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

import pyodbc

import app_config
from database_common import (
    DatabaseLockError,
    DocumentConflictError,
    FileStorageMixin,
    row_to_dict,
    rows_to_dicts,
)
from sqlserver_schema import execute_schema_statements, seed_reference_data


class SqlRow(Mapping[str, Any]):
    def __init__(self, columns: Sequence[str], values: Sequence[Any]):
        self._values = list(values)
        self._mapping = {column: values[index] for index, column in enumerate(columns)}

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._mapping[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._mapping)

    def __len__(self) -> int:
        return len(self._mapping)


class SqlServerCursorAdapter:
    def __init__(self, manager: "SqlServerDatabaseManager", cursor: pyodbc.Cursor):
        self._manager = manager
        self._cursor = cursor
        self.description = cursor.description

    @property
    def raw_cursor(self) -> pyodbc.Cursor:
        return self._cursor

    def execute(self, query: str, params: Optional[Iterable[Any]] = None):
        normalized_query, normalized_params = self._manager.normalize_sql(query, params)
        self._cursor.execute(normalized_query, normalized_params)
        self.description = self._cursor.description
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        return self._manager.wrap_row(self._cursor, row)

    def fetchall(self):
        rows = self._cursor.fetchall()
        return self._manager.wrap_rows(self._cursor, rows)

    def __iter__(self):
        for row in self._cursor:
            yield self._manager.wrap_row(self._cursor, row)

    def __getattr__(self, item: str):
        return getattr(self._cursor, item)


class SqlServerConnectionAdapter:
    def __init__(self, manager: "SqlServerDatabaseManager"):
        self._manager = manager

    def cursor(self) -> SqlServerCursorAdapter:
        return SqlServerCursorAdapter(self._manager, self._manager.conn.cursor())

    def execute(self, query: str, params: Optional[Iterable[Any]] = None) -> SqlServerCursorAdapter:
        cursor = self.cursor()
        cursor.execute(query, params)
        return cursor

    def commit(self) -> None:
        self._manager.conn.commit()

    def rollback(self) -> None:
        self._manager.conn.rollback()

    def close(self) -> None:
        self._manager.conn.close()

    def __getattr__(self, item: str):
        return getattr(self._manager.conn, item)


class SqlServerDatabaseManager(FileStorageMixin):
    backend = "sqlserver"
    DRIVER_PREFERENCE = (
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 13 for SQL Server",
        "SQL Server Native Client 11.0",
        "SQL Server",
    )

    @staticmethod
    def _get_config_value(config: Dict[str, Any], key: str, default: Any = None) -> Any:
        value = config.get(key, default)
        return value if value not in (None, "") else default

    @classmethod
    def build_server_name(cls, config: Dict[str, Any]) -> str:
        server = str(cls._get_config_value(config, "server", "") or "").strip()
        if not server:
            raise ValueError("Не указан SQL Server сервер")
        port = int(cls._get_config_value(config, "port", 1433) or 1433)
        if port and "," not in server and "\\" not in server:
            return f"{server},{port}"
        return server

    @classmethod
    def _available_drivers(cls) -> List[str]:
        try:
            return list(pyodbc.drivers())
        except Exception:
            return []

    @classmethod
    def resolve_driver(cls, config: Dict[str, Any]) -> str:
        configured_driver = str(cls._get_config_value(config, "driver", "") or "").strip()
        available = cls._available_drivers()

        candidates: List[str] = []
        if configured_driver:
            candidates.append(configured_driver)
        candidates.extend(cls.DRIVER_PREFERENCE)

        seen = set()
        for candidate in candidates:
            if candidate and candidate not in seen:
                seen.add(candidate)
                if candidate in available:
                    return candidate

        available_text = ", ".join(available) if available else "не найдено ни одного ODBC-драйвера"
        raise ValueError(
            "На этом компьютере не найден подходящий ODBC-драйвер для SQL Server. "
            f"Доступные драйверы: {available_text}. "
            "Установите 'ODBC Driver 18 for SQL Server' или 'ODBC Driver 17 for SQL Server'."
        )

    @classmethod
    def build_connection_string(cls, config: Dict[str, Any], database_override: Optional[str] = None) -> str:
        password = cls._resolve_password(config)
        driver = cls.resolve_driver(config)
        encrypt = bool(cls._get_config_value(config, "encrypt", True))
        trust_server_certificate = bool(cls._get_config_value(config, "trust_server_certificate", True))
        timeout = int(cls._get_config_value(config, "timeout", 5) or 5)
        database = database_override or str(cls._get_config_value(config, "database", "") or "").strip()
        user = str(cls._get_config_value(config, "user", "") or "").strip()
        connection_parts = [
            f"DRIVER={{{driver}}}",
            f"SERVER={cls.build_server_name(config)}",
            f"DATABASE={database}",
            f"UID={user}",
            f"PWD={password}",
            f"Connection Timeout={timeout}",
        ]

        supports_modern_options = "ODBC Driver" in driver or "Native Client" in driver
        if supports_modern_options:
            connection_parts.extend(
                [
                    f"Encrypt={'yes' if encrypt else 'no'}",
                    f"TrustServerCertificate={'yes' if trust_server_certificate else 'no'}",
                    "MARS_Connection=yes",
                ]
            )

        return ";".join(connection_parts)

    @classmethod
    def ensure_database_exists(cls, config: Dict[str, Any]) -> None:
        database_name = str(cls._get_config_value(config, "database", "") or "").strip()
        if not database_name:
            raise ValueError("Не указано имя базы данных SQL Server")

        safe_literal = database_name.replace("'", "''")
        safe_identifier = database_name.replace("]", "]]")
        connection = pyodbc.connect(cls.build_connection_string(config, database_override="master"), autocommit=True)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    f"IF DB_ID(N'{safe_literal}') IS NULL BEGIN EXEC(N'CREATE DATABASE [{safe_identifier}]') END"
                )
            except pyodbc.ProgrammingError as exc:
                raise ValueError(
                    "Не удалось создать базу SQL Server. Для создания новой базы нужен логин "
                    "с правом CREATE DATABASE или администратор SQL Server."
                ) from exc
        finally:
            connection.close()

    def __init__(self, config: Dict[str, Any], create_if_not_exists: bool = False):
        self.app_root = app_config.get_app_root()
        self.config = dict(config or {})
        self.profile_name = self.config.get("profile_name") or self.config.get("name") or "sqlserver"
        self.server = (self.config.get("server") or "").strip()
        self.port = int(self.config.get("port") or 1433)
        self.database = (self.config.get("database") or "").strip()
        self.user = (self.config.get("user") or "").strip()
        self.password = self._resolve_password(self.config)
        self.driver = self.config.get("driver") or "ODBC Driver 18 for SQL Server"
        self.encrypt = bool(self.config.get("encrypt", True))
        self.trust_server_certificate = bool(self.config.get("trust_server_certificate", True))
        self.timeout = int(self.config.get("timeout") or 5)
        self.db_path = f"sqlserver://{self.server}:{self.port}/{self.database}"
        self.conn: Optional[pyodbc.Connection] = None
        self.connection = None
        self.cursor = None

        self.setup_file_storage(
            self.profile_name,
            files_root=self.config.get("files_root"),
            storage_subdir=self.config.get("storage_subdir"),
            allow_local_fallback=False,
        )
        self.connect()
        if create_if_not_exists or self.config.get("initialize_schema", True):
            self.create_tables_if_not_exist()

    @staticmethod
    def _resolve_password(config: Dict[str, Any]) -> str:
        direct_password = (config.get("password") or "").strip()
        if direct_password:
            return direct_password

        password_env = (config.get("password_env") or "").strip()
        profile_name = str(config.get("profile_name") or config.get("name") or "sqlserver").upper()
        for env_name in (
            password_env,
            f"ACTI_SQLSERVER_PASSWORD_{profile_name}",
            "ACTI_SQLSERVER_PASSWORD",
        ):
            if env_name:
                value = os.getenv(env_name, "").strip()
                if value:
                    return value

        raise ValueError(
            "Не настроен пароль SQL Server. Укажите пароль в профиле или задайте "
            "переменную ACTI_SQLSERVER_PASSWORD / ACTI_SQLSERVER_PASSWORD_<PROFILE>."
        )

    def _build_server_name(self) -> str:
        return self.build_server_name(self.config)

    def _build_connection_string(self) -> str:
        return self.build_connection_string(self.config)

    def connect(self) -> None:
        resolved_driver = self.resolve_driver(self.config)
        self.driver = resolved_driver
        self.config["driver"] = resolved_driver
        self.conn = pyodbc.connect(self._build_connection_string(), autocommit=False)
        self.connection = SqlServerConnectionAdapter(self)
        self.cursor = self.connection.cursor()
        print(f"[sqlserver] connected to {self._build_server_name()} / {self.database} via {self.driver}")

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    @staticmethod
    def _normalize_id_list(values: Optional[Iterable[Any]]) -> List[int]:
        normalized: List[int] = []
        for value in values or []:
            if value in (None, "", 0, "0"):
                continue
            normalized.append(int(value))
        return sorted(set(normalized))

    @staticmethod
    def _normalize_snapshot_value(value: Any) -> Any:
        if isinstance(value, bytes):
            return value.hex()
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat(sep=" ", timespec="seconds")
        return value

    def wrap_row(self, cursor: pyodbc.Cursor, row: Any):
        if row is None:
            return None
        columns = [column[0] for column in (cursor.description or [])]
        return SqlRow(columns, row)

    def wrap_rows(self, cursor: pyodbc.Cursor, rows: Iterable[Any]) -> List[SqlRow]:
        columns = [column[0] for column in (cursor.description or [])]
        return [SqlRow(columns, row) for row in rows]

    @contextmanager
    def transaction(self):
        cursor = SqlServerCursorAdapter(self, self.conn.cursor())
        try:
            yield cursor
            self.conn.commit()
        except pyodbc.Error as exc:
            self.conn.rollback()
            if "deadlock" in str(exc).lower() or "timeout" in str(exc).lower():
                raise DatabaseLockError(str(exc)) from exc
            raise
        except Exception:
            self.conn.rollback()
            raise

    def create_tables_if_not_exist(self) -> None:
        with self.transaction() as cursor:
            execute_schema_statements(cursor.raw_cursor)
            seed_reference_data(cursor.raw_cursor)

    def normalize_sql(self, query: str, params: Optional[Iterable[Any]]) -> Tuple[str, Tuple[Any, ...]]:
        sql = (query or "").strip()
        normalized_params = tuple(params or ())
        if not sql:
            return sql, normalized_params

        if sql.upper().startswith("PRAGMA"):
            raise RuntimeError("SQLite PRAGMA is not supported by SQL Server backend")

        sql = re.sub(r"\bCURRENT_TIMESTAMP\b", "SYSUTCDATETIME()", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bCOLLATE\s+NOCASE\b", "COLLATE Latin1_General_CI_AI", sql, flags=re.IGNORECASE)
        sql = re.sub(
            r"date\(([^)]+)\)\s+BETWEEN\s+date\(\?\)\s+AND\s+date\(\?\)",
            r"TRY_CONVERT(date, \1, 23) BETWEEN TRY_CONVERT(date, ?, 23) AND TRY_CONVERT(date, ?, 23)",
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r"date\(([^)]+)\)\s*>=\s*date\(\?\)",
            r"TRY_CONVERT(date, \1, 23) >= TRY_CONVERT(date, ?, 23)",
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r"date\(([^)]+)\)\s*<=\s*date\(\?\)",
            r"TRY_CONVERT(date, \1, 23) <= TRY_CONVERT(date, ?, 23)",
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r"date\(([^)]+)\)\s*=\s*date\(\?\)",
            r"TRY_CONVERT(date, \1, 23) = TRY_CONVERT(date, ?, 23)",
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r"strftime\('%Y',\s*([^)]+)\)\s*=\s*\?",
            r"YEAR(TRY_CONVERT(date, \1, 23)) = ?",
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r"strftime\('%m',\s*([^)]+)\)\s*=\s*\?",
            r"RIGHT('0' + CAST(MONTH(TRY_CONVERT(date, \1, 23)) AS VARCHAR(2)), 2) = ?",
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r"CAST\(strftime\('%m',\s*([^)]+)\)\s+AS\s+INTEGER\)\s*=\s*\?",
            r"MONTH(TRY_CONVERT(date, \1, 23)) = ?",
            sql,
            flags=re.IGNORECASE,
        )
        insert_or_ignore = re.match(
            r"INSERT\s+OR\s+IGNORE\s+INTO\s+([A-Za-z_][\w]*)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)",
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if insert_or_ignore and normalized_params:
            table_name = insert_or_ignore.group(1)
            columns = [column.strip() for column in insert_or_ignore.group(2).split(",")]
            values_sql = insert_or_ignore.group(3).strip()
            where_sql = " AND ".join(f"{column} = ?" for column in columns)
            sql = (
                f"IF NOT EXISTS (SELECT 1 FROM {table_name} WHERE {where_sql}) "
                f"BEGIN INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({values_sql}) END"
            )
            normalized_params = tuple(normalized_params) + tuple(normalized_params)

        limit_offset_match = re.search(r"LIMIT\s+\?\s+OFFSET\s+\?", sql, flags=re.IGNORECASE)
        if limit_offset_match:
            replacement = "OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"
            if not re.search(r"ORDER\s+BY", sql, flags=re.IGNORECASE):
                replacement = f"ORDER BY (SELECT NULL) {replacement}"
            sql = re.sub(r"LIMIT\s+\?\s+OFFSET\s+\?", replacement, sql, flags=re.IGNORECASE)
            if len(normalized_params) >= 2:
                params_list = list(normalized_params)
                params_list[-2], params_list[-1] = params_list[-1], params_list[-2]
                normalized_params = tuple(params_list)

        constant_limit_offset = re.search(r"LIMIT\s+(\d+)\s+OFFSET\s+(\d+)", sql, flags=re.IGNORECASE)
        if constant_limit_offset:
            limit_value = constant_limit_offset.group(1)
            offset_value = constant_limit_offset.group(2)
            replacement = f"OFFSET {offset_value} ROWS FETCH NEXT {limit_value} ROWS ONLY"
            if not re.search(r"ORDER\s+BY", sql, flags=re.IGNORECASE):
                replacement = f"ORDER BY (SELECT NULL) {replacement}"
            sql = re.sub(r"LIMIT\s+\d+\s+OFFSET\s+\d+", replacement, sql, flags=re.IGNORECASE)

        parameterized_limit = re.search(r"LIMIT\s+\?$", sql, flags=re.IGNORECASE)
        if parameterized_limit:
            if re.search(r"ORDER\s+BY", sql, flags=re.IGNORECASE):
                sql = re.sub(
                    r"LIMIT\s+\?$",
                    "OFFSET 0 ROWS FETCH NEXT ? ROWS ONLY",
                    sql,
                    flags=re.IGNORECASE,
                )
            else:
                sql = re.sub(r"^SELECT", "SELECT TOP (?)", sql, count=1, flags=re.IGNORECASE)
                sql = re.sub(r"\s+LIMIT\s+\?$", "", sql, flags=re.IGNORECASE)
                if normalized_params:
                    params_list = list(normalized_params)
                    limit_value = params_list.pop()
                    normalized_params = (limit_value, *params_list)

        constant_limit = re.search(r"LIMIT\s+(\d+)$", sql, flags=re.IGNORECASE)
        if constant_limit:
            limit_value = constant_limit.group(1)
            if re.search(r"ORDER\s+BY", sql, flags=re.IGNORECASE):
                sql = re.sub(
                    r"LIMIT\s+\d+$",
                    f"OFFSET 0 ROWS FETCH NEXT {limit_value} ROWS ONLY",
                    sql,
                    flags=re.IGNORECASE,
                )
            else:
                sql = re.sub(r"^SELECT", f"SELECT TOP {limit_value}", sql, count=1, flags=re.IGNORECASE)
                sql = re.sub(r"\s+LIMIT\s+\d+$", "", sql, flags=re.IGNORECASE)

        return sql, normalized_params

    def _fetch_identity(self, cursor: SqlServerCursorAdapter) -> Optional[int]:
        cursor.raw_cursor.execute("SELECT CAST(SCOPE_IDENTITY() AS INT)")
        row = cursor.raw_cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def execute_query(self, query: str, params: Optional[Iterable[Any]] = None) -> List[Dict[str, Any]]:
        cursor = self.connection.cursor()
        cursor.execute(query, params or ())
        return rows_to_dicts(cursor, cursor.fetchall())

    def execute_update(self, query: str, params: Optional[Iterable[Any]] = None) -> Optional[int]:
        with self.transaction() as cursor:
            normalized_query, normalized_params = self.normalize_sql(query, params or ())
            is_plain_insert = bool(re.match(r"^\s*INSERT\s+INTO\b", normalized_query, flags=re.IGNORECASE))
            if is_plain_insert and "OUTPUT INSERTED.ID" not in normalized_query.upper():
                output_query = re.sub(
                    r"\bVALUES\b",
                    "OUTPUT INSERTED.id VALUES",
                    normalized_query,
                    count=1,
                    flags=re.IGNORECASE,
                )
                cursor.raw_cursor.execute(output_query, normalized_params)
                row = cursor.raw_cursor.fetchone()
                return int(row[0]) if row and row[0] is not None else None

            cursor.raw_cursor.execute(normalized_query, normalized_params)
            return None

    def get_last_backup_date(self) -> Optional[str]:
        rows = self.execute_query("SELECT last_backup_date FROM db_metadata WHERE id = 1")
        value = rows[0]["last_backup_date"] if rows else None
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        return value

    def update_backup_date(self):
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.transaction() as cursor:
            cursor.execute(
                """
                UPDATE db_metadata
                SET last_backup_date = ?, backup_count = backup_count + 1, updated_at = SYSUTCDATETIME()
                WHERE id = 1
                """,
                (current_time,),
            )
        return True

    def get_backup_statistics(self) -> Dict[str, Any]:
        rows = self.execute_query(
            """
            SELECT
                last_backup_date,
                backup_count,
                DATEDIFF(DAY, last_backup_date, SYSUTCDATETIME()) AS days_since
            FROM db_metadata
            WHERE id = 1
            """
        )
        if not rows:
            return {"last_backup": None, "backup_count": 0, "days_since_backup": None}
        result = rows[0]
        backup_date = result.get("last_backup_date")
        if isinstance(backup_date, datetime):
            backup_date = backup_date.strftime("%Y-%m-%d %H:%M:%S")
        return {
            "last_backup": backup_date,
            "backup_count": result.get("backup_count", 0) or 0,
            "days_since_backup": result.get("days_since"),
        }

    @staticmethod
    def _get_updatable_document_fields() -> List[str]:
        return [
            "reg_number",
            "reg_date",
            "number",
            "status_id",
            "type_id",
            "signing_type_id",
            "document_kind_id",
            "theme_id",
            "executor_id",
            "responsible_executor_id",
            "title",
            "document_path",
            "should_publish",
            "published_where_id",
            "published_date",
            "control_date",
            "removed_from_control",
            "execution_result",
            "pages_count",
            "attachments_count",
            "case_number",
            "volume_number",
            "sheets",
        ]

    def _insert_document_relations(
        self,
        cursor: SqlServerCursorAdapter,
        table_name: str,
        document_id: int,
        column_name: str,
        values: Iterable[int],
    ) -> None:
        for value in self._normalize_id_list(values):
            cursor.raw_cursor.execute(
                f"""
                IF NOT EXISTS (
                    SELECT 1 FROM {table_name}
                    WHERE document_id = ? AND {column_name} = ?
                )
                BEGIN
                    INSERT INTO {table_name} (document_id, {column_name}) VALUES (?, ?)
                END
                """,
                (document_id, value, document_id, value),
            )

    def add_document(self, data: Dict[str, Any]) -> int:
        fields = {
            "reg_number": data.get("reg_number", ""),
            "reg_date": data.get("reg_date"),
            "number": data.get("number", ""),
            "status_id": data.get("status_id"),
            "type_id": data.get("type_id"),
            "signing_type_id": data.get("signing_type_id"),
            "document_kind_id": data.get("document_kind_id"),
            "theme_id": data.get("theme_id"),
            "executor_id": data.get("executor_id"),
            "responsible_executor_id": data.get("responsible_executor_id"),
            "title": data.get("title", ""),
            "document_path": data.get("document_path", ""),
            "should_publish": data.get("should_publish", ""),
            "published_where_id": data.get("published_where_id"),
            "published_date": data.get("published_date"),
            "control_date": data.get("control_date"),
            "removed_from_control": data.get("removed_from_control", ""),
            "execution_result": data.get("execution_result", ""),
            "pages_count": data.get("pages_count"),
            "attachments_count": data.get("attachments_count"),
            "case_number": data.get("case_number", ""),
            "volume_number": data.get("volume_number", ""),
            "sheets": data.get("sheets", ""),
        }
        columns = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)

        with self.transaction() as cursor:
            cursor.raw_cursor.execute(
                f"INSERT INTO documents ({columns}) OUTPUT INSERTED.id VALUES ({placeholders})",
                tuple(fields.values()),
            )
            row = cursor.raw_cursor.fetchone()
            document_id = int(row[0]) if row and row[0] is not None else None
            self._insert_document_relations(cursor, "document_signers", document_id, "signer_id", data.get("signers"))
            self._insert_document_relations(cursor, "document_approvers", document_id, "approver_id", data.get("approvers"))
        return int(document_id)

    def _get_document_edit_snapshot_with_cursor(self, cursor: SqlServerCursorAdapter, document_id: int) -> Dict[str, Any]:
        fields = self._get_updatable_document_fields()
        query = f"SELECT {', '.join(fields)}, row_version FROM documents WHERE id = ?"
        cursor.execute(query, (document_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Document with ID {document_id} not found")

        snapshot = {field: self._normalize_snapshot_value(row[field]) for field in fields}
        snapshot["_row_version"] = self._normalize_snapshot_value(row["row_version"])

        cursor.execute("SELECT signer_id FROM document_signers WHERE document_id = ?", (document_id,))
        snapshot["signers"] = self._normalize_id_list([item[0] for item in cursor.fetchall()])

        cursor.execute("SELECT approver_id FROM document_approvers WHERE document_id = ?", (document_id,))
        snapshot["approvers"] = self._normalize_id_list([item[0] for item in cursor.fetchall()])
        return snapshot

    def get_document_edit_snapshot(self, document_id: int) -> Dict[str, Any]:
        cursor = self.connection.cursor()
        return self._get_document_edit_snapshot_with_cursor(cursor, document_id)

    def _assert_no_snapshot_conflict(
        self,
        cursor: SqlServerCursorAdapter,
        document_id: int,
        expected_snapshot: Dict[str, Any],
    ) -> None:
        current_snapshot = self._get_document_edit_snapshot_with_cursor(cursor, document_id)
        normalized_expected: Dict[str, Any] = {}
        for key, value in (expected_snapshot or {}).items():
            if key in ("signers", "approvers"):
                normalized_expected[key] = self._normalize_id_list(value)
            else:
                normalized_expected[key] = self._normalize_snapshot_value(value)

        if current_snapshot != normalized_expected:
            raise DocumentConflictError(
                "Document has been modified by another user.",
                current_snapshot=current_snapshot,
            )

    def update_document(
        self,
        document_id: int,
        data: Dict[str, Any],
        expected_snapshot: Optional[Dict[str, Any]] = None,
        force_overwrite: bool = False,
    ):
        with self.transaction() as cursor:
            if expected_snapshot and not force_overwrite:
                self._assert_no_snapshot_conflict(cursor, document_id, expected_snapshot)

            set_parts: List[str] = []
            values: List[Any] = []
            for field in self._get_updatable_document_fields():
                if field in data:
                    set_parts.append(f"{field} = ?")
                    values.append(data[field])

            if set_parts:
                values.append(document_id)
                cursor.raw_cursor.execute(
                    f"UPDATE documents SET {', '.join(set_parts)} WHERE id = ?",
                    tuple(values),
                )

            if "signers" in data:
                cursor.raw_cursor.execute("DELETE FROM document_signers WHERE document_id = ?", (document_id,))
                self._insert_document_relations(cursor, "document_signers", document_id, "signer_id", data.get("signers"))

            if "approvers" in data:
                cursor.raw_cursor.execute("DELETE FROM document_approvers WHERE document_id = ?", (document_id,))
                self._insert_document_relations(cursor, "document_approvers", document_id, "approver_id", data.get("approvers"))

    def delete_document(self, doc_id: int):
        with self.transaction() as cursor:
            cursor.raw_cursor.execute("DELETE FROM document_signers WHERE document_id = ?", (doc_id,))
            cursor.raw_cursor.execute("DELETE FROM document_approvers WHERE document_id = ?", (doc_id,))
            cursor.raw_cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    def get_document_by_id(self, doc_id: int) -> Dict[str, Any]:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            SELECT
                d.*,
                s.name as status_name,
                dt.name as type_name,
                st.name as signing_type_name,
                dk.name as document_kind_name,
                t.name as theme_name,
                e.name as executor_name,
                re.name as responsible_executor_name,
                pw.name as published_where_name
            FROM documents d
            LEFT JOIN ref_status s ON d.status_id = s.id
            LEFT JOIN ref_document_types dt ON d.type_id = dt.id
            LEFT JOIN ref_signing_types st ON d.signing_type_id = st.id
            LEFT JOIN ref_document_kinds dk ON d.document_kind_id = dk.id
            LEFT JOIN ref_themes t ON d.theme_id = t.id
            LEFT JOIN ref_executors e ON d.executor_id = e.id
            LEFT JOIN ref_responsible_executors re ON d.responsible_executor_id = re.id
            LEFT JOIN ref_published_where pw ON d.published_where_id = pw.id
            WHERE d.id = ?
            """,
            (doc_id,),
        )
        row = cursor.fetchone()
        if not row:
            return {}

        document = row_to_dict(cursor, row)
        if isinstance(document.get("row_version"), bytes):
            document["row_version"] = document["row_version"].hex()
        document["signers"] = self.get_document_signers(doc_id)
        document["approvers"] = self.get_document_approvers(doc_id)
        return document

    def get_documents_count(self) -> int:
        rows = self.execute_query("SELECT COUNT(*) AS total_count FROM documents")
        return int(rows[0]["total_count"]) if rows else 0

    def _document_date_expr(self) -> str:
        return (
            "COALESCE("
            "TRY_CONVERT(date, d.reg_date, 23), "
            "TRY_CONVERT(date, d.reg_date, 104), "
            "TRY_CONVERT(date, d.reg_date)"
            ")"
        )

    def _append_document_filter_clauses(
        self,
        where_clauses: List[str],
        params: List[Any],
        filters: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not filters:
            return

        date_expr = self._document_date_expr()

        search_text = str(filters.get("search_text") or "").strip()
        if search_text:
            where_clauses.append("(d.title LIKE ? OR d.reg_number LIKE ? OR d.number LIKE ? OR d.document_path LIKE ?)")
            pattern = f"%{search_text}%"
            params.extend([pattern, pattern, pattern, pattern])

        text_filters = {
            "reg_number": "d.reg_number",
            "number": "d.number",
            "title": "d.title",
            "status": "s.name",
            "type": "dt.name",
            "document_kind": "dk.name",
            "executor": "e.name",
            "theme": "t.name",
        }
        for filter_name, column_name in text_filters.items():
            value = str(filters.get(filter_name) or "").strip()
            if value:
                where_clauses.append(f"{column_name} LIKE ?")
                params.append(f"%{value}%")

        if filters.get("status_id"):
            where_clauses.append("d.status_id = ?")
            params.append(filters["status_id"])
        if filters.get("type_id"):
            where_clauses.append("d.type_id = ?")
            params.append(filters["type_id"])
        if filters.get("document_kind_id"):
            where_clauses.append("d.document_kind_id = ?")
            params.append(filters["document_kind_id"])
        if filters.get("executor_id"):
            where_clauses.append("d.executor_id = ?")
            params.append(filters["executor_id"])
        if filters.get("theme_id"):
            where_clauses.append("d.theme_id = ?")
            params.append(filters["theme_id"])

        if filters.get("date_exact"):
            where_clauses.append(f"{date_expr} = TRY_CONVERT(date, ?, 23)")
            params.append(filters["date_exact"])
        else:
            if filters.get("date_from"):
                where_clauses.append(f"{date_expr} >= TRY_CONVERT(date, ?, 23)")
                params.append(filters["date_from"])
            if filters.get("date_to"):
                where_clauses.append(f"{date_expr} <= TRY_CONVERT(date, ?, 23)")
                params.append(filters["date_to"])

        if filters.get("year"):
            where_clauses.append(f"YEAR({date_expr}) = ?")
            params.append(int(filters["year"]))
            if filters.get("month"):
                where_clauses.append(f"MONTH({date_expr}) = ?")
                params.append(int(filters["month"]))

    def get_documents_paginated(self, page: int = 1, per_page: int = 100, filters: Optional[Dict[str, Any]] = None) -> tuple:
        offset = (page - 1) * per_page
        where_clauses: List[str] = []
        params: List[Any] = []

        self._append_document_filter_clauses(where_clauses, params, filters)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        count_rows = self.execute_query(
            f"""
            SELECT COUNT(*) AS total_count
            FROM documents d
            LEFT JOIN ref_status s ON d.status_id = s.id
            LEFT JOIN ref_document_types dt ON d.type_id = dt.id
            LEFT JOIN ref_document_kinds dk ON d.document_kind_id = dk.id
            LEFT JOIN ref_executors e ON d.executor_id = e.id
            LEFT JOIN ref_themes t ON d.theme_id = t.id
            {where_sql}
            """,
            tuple(params),
        )
        total_count = int(count_rows[0]["total_count"]) if count_rows else 0

        query = f"""
            SELECT
                d.id,
                d.title,
                d.reg_number,
                d.reg_date,
                COALESCE(s.name, 'Не указан') as status_name,
                COALESCE(dt.name, 'Не указан') as type_name,
                COALESCE(e.name, 'Не назначен') as executor_name,
                COALESCE(t.name, 'Не указана') as theme_name
            FROM documents d
            LEFT JOIN ref_status s ON d.status_id = s.id
            LEFT JOIN ref_document_types dt ON d.type_id = dt.id
            LEFT JOIN ref_document_kinds dk ON d.document_kind_id = dk.id
            LEFT JOIN ref_executors e ON d.executor_id = e.id
            LEFT JOIN ref_themes t ON d.theme_id = t.id
            {where_sql}
            ORDER BY
                CASE WHEN d.reg_date IS NULL OR LTRIM(RTRIM(d.reg_date)) = '' THEN 0 ELSE 1 END ASC,
                d.reg_date DESC,
                d.id DESC
            LIMIT ? OFFSET ?
        """
        rows = self.execute_query(query, tuple(params + [per_page, offset]))
        has_more = (offset + per_page) < total_count
        return rows, total_count, has_more

    def search_documents(self, keyword: str, limit: int = 100) -> List[Dict[str, Any]]:
        pattern = f"%{keyword}%"
        return self.execute_query(
            """
            SELECT
                d.id,
                d.reg_number,
                d.reg_date,
                d.title,
                s.name as status_name,
                dt.name as type_name,
                e.name as executor_name,
                t.name as theme_name
            FROM documents d
            LEFT JOIN ref_status s ON d.status_id = s.id
            LEFT JOIN ref_document_types dt ON d.type_id = dt.id
            LEFT JOIN ref_executors e ON d.executor_id = e.id
            LEFT JOIN ref_themes t ON d.theme_id = t.id
            WHERE
                d.title LIKE ? OR
                d.reg_number LIKE ? OR
                d.document_path LIKE ? OR
                e.name LIKE ? OR
                t.name LIKE ?
            ORDER BY d.reg_date DESC
            LIMIT ?
            """,
            (pattern, pattern, pattern, pattern, pattern, limit),
        )

    def search_documents_with_filters(self, filters):
        query = """
            SELECT
                d.id, d.title, d.reg_number, d.reg_date,
                COALESCE(s.name, 'Не указан') as status,
                COALESCE(dt.name, 'Не указан') as type_doc,
                d.document_path as filename,
                COALESCE(e.name, 'Не назначен') as executor_name,
                COALESCE(t.name, 'Не указана') as theme_name
            FROM documents d
            LEFT JOIN ref_status s ON d.status_id = s.id
            LEFT JOIN ref_document_types dt ON d.type_id = dt.id
            LEFT JOIN ref_executors e ON d.executor_id = e.id
            LEFT JOIN ref_themes t ON d.theme_id = t.id
            WHERE 1=1
        """
        params = []
        if filters.get("search_text"):
            query += " AND (d.title LIKE ? OR d.reg_number LIKE ?)"
            pattern = f"%{filters['search_text']}%"
            params.extend([pattern, pattern])
        if filters.get("status"):
            query += " AND LOWER(s.name) LIKE ?"
            params.append(f"%{filters['status']}%")
        if filters.get("year") and filters.get("month"):
            query += " AND strftime('%Y', d.reg_date) = ? AND strftime('%m', d.reg_date) = ?"
            params.extend([str(filters["year"]), f"{filters['month']:02d}"])
        elif filters.get("year"):
            query += " AND strftime('%Y', d.reg_date) = ?"
            params.append(str(filters["year"]))
        if filters.get("date_from") and filters.get("date_to"):
            query += " AND date(d.reg_date) BETWEEN date(?) AND date(?)"
            params.extend([filters["date_from"], filters["date_to"]])
        query += " ORDER BY d.reg_date DESC, d.id DESC LIMIT 1000"
        return self.execute_query(query, tuple(params))

    def search_by_tags(self, **kwargs) -> List[Dict[str, Any]]:
        query = """
            SELECT
                d.id,
                d.reg_number,
                d.reg_date,
                d.title,
                d.number,
                d.document_path as filepath,
                s.name as status,
                dt.name as type_doc,
                dk.name as document_kind,
                st.name as signing_type,
                e.name as executor_name,
                re.name as responsible_executor_name,
                t.name as theme_name,
                pw.name as published_where,
                d.should_publish,
                d.case_number,
                d.volume_number,
                d.removed_from_control
            FROM documents d
            LEFT JOIN ref_status s ON d.status_id = s.id
            LEFT JOIN ref_document_types dt ON d.type_id = dt.id
            LEFT JOIN ref_document_kinds dk ON d.document_kind_id = dk.id
            LEFT JOIN ref_signing_types st ON d.signing_type_id = st.id
            LEFT JOIN ref_executors e ON d.executor_id = e.id
            LEFT JOIN ref_responsible_executors re ON d.responsible_executor_id = re.id
            LEFT JOIN ref_themes t ON d.theme_id = t.id
            LEFT JOIN ref_published_where pw ON d.published_where_id = pw.id
            WHERE 1=1
        """
        params: List[Any] = []
        if kwargs.get("keywords"):
            pattern = f"%{kwargs['keywords']}%"
            query += """
                AND (
                    d.title LIKE ? OR
                    d.reg_number LIKE ? OR
                    d.number LIKE ? OR
                    e.name LIKE ? OR
                    t.name LIKE ?
                )
            """
            params.extend([pattern] * 5)
        if kwargs.get("date_from"):
            query += " AND date(d.reg_date) >= date(?)"
            params.append(kwargs["date_from"])
        if kwargs.get("date_to"):
            query += " AND date(d.reg_date) <= date(?)"
            params.append(kwargs["date_to"])
        if kwargs.get("status"):
            query += " AND s.name = ?"
            params.append(kwargs["status"])
        if kwargs.get("type_doc"):
            query += " AND dt.name = ?"
            params.append(kwargs["type_doc"])
        if kwargs.get("document_kind"):
            query += " AND dk.name = ?"
            params.append(kwargs["document_kind"])
        if kwargs.get("signing_type"):
            query += " AND st.name = ?"
            params.append(kwargs["signing_type"])
        if kwargs.get("executor_ids"):
            placeholders = ",".join("?" for _ in kwargs["executor_ids"])
            query += f" AND d.executor_id IN ({placeholders})"
            params.extend(kwargs["executor_ids"])
        if kwargs.get("theme_ids"):
            placeholders = ",".join("?" for _ in kwargs["theme_ids"])
            query += f" AND d.theme_id IN ({placeholders})"
            params.extend(kwargs["theme_ids"])
        if "should_publish" in kwargs:
            query += " AND d.should_publish = ?"
            params.append(kwargs["should_publish"])
        if kwargs.get("published_where_id"):
            query += " AND d.published_where_id = ?"
            params.append(kwargs["published_where_id"])
        if kwargs.get("case_number"):
            query += " AND d.case_number LIKE ?"
            params.append(f"%{kwargs['case_number']}%")
        if kwargs.get("volume_number"):
            query += " AND d.volume_number LIKE ?"
            params.append(f"%{kwargs['volume_number']}%")
        if "removed_from_control" in kwargs:
            query += " AND d.removed_from_control = ?"
            params.append(kwargs["removed_from_control"])
        query += " ORDER BY d.reg_date DESC LIMIT 1000"
        return self.execute_query(query, tuple(params))

    def get_statistics(self) -> Dict[str, Any]:
        return {
            "total_documents": self.get_documents_count(),
            "by_status": self.execute_query(
                """
                SELECT s.name, COUNT(d.id) as count
                FROM ref_status s
                LEFT JOIN documents d ON s.id = d.status_id
                GROUP BY s.id, s.name
                ORDER BY count DESC
                """
            ),
            "by_type": self.execute_query(
                """
                SELECT dt.name, COUNT(d.id) as count
                FROM ref_document_types dt
                LEFT JOIN documents d ON dt.id = d.type_id
                GROUP BY dt.id, dt.name
                ORDER BY count DESC
                """
            ),
        }

    def get_documents_statistics(self):
        stats = {"total_documents": self.get_documents_count(), "by_status": {}, "by_type": {}, "by_executor": {}}
        for item in self.execute_query(
            """
            SELECT s.name, COUNT(d.id) as count
            FROM ref_status s
            LEFT JOIN documents d ON s.id = d.status_id
            GROUP BY s.id, s.name
            ORDER BY count DESC
            """
        ):
            stats["by_status"][item["name"]] = item["count"]
        for item in self.execute_query(
            """
            SELECT dt.name, COUNT(d.id) as count
            FROM ref_document_types dt
            LEFT JOIN documents d ON dt.id = d.type_id
            GROUP BY dt.id, dt.name
            ORDER BY count DESC
            """
        ):
            stats["by_type"][item["name"]] = item["count"]
        for item in self.execute_query(
            """
            SELECT e.name, COUNT(d.id) as count
            FROM ref_executors e
            LEFT JOIN documents d ON e.id = d.executor_id
            GROUP BY e.id, e.name
            HAVING COUNT(d.id) > 0
            ORDER BY count DESC
            LIMIT 10
            """
        ):
            stats["by_executor"][item["name"]] = item["count"]
        return stats

    def get_compact_documents(
        self,
        filters: Optional[Dict[str, Any]] = None,
        document_ids: Optional[Iterable[Any]] = None,
        limit: Optional[int] = 100,
    ) -> List[Dict[str, Any]]:
        query = """
            SELECT
                d.id,
                d.title,
                d.reg_number,
                d.reg_date
            FROM documents d
            LEFT JOIN ref_status s ON d.status_id = s.id
            LEFT JOIN ref_document_types dt ON d.type_id = dt.id
            LEFT JOIN ref_document_kinds dk ON d.document_kind_id = dk.id
            LEFT JOIN ref_executors e ON d.executor_id = e.id
            LEFT JOIN ref_themes t ON d.theme_id = t.id
            WHERE 1=1
        """
        params: List[Any] = []

        id_list = self._normalize_id_list(document_ids)
        if id_list:
            placeholders = ", ".join("?" for _ in id_list)
            query += f" AND d.id IN ({placeholders})"
            params.extend(id_list)

        where_clauses: List[str] = []
        self._append_document_filter_clauses(where_clauses, params, dict(filters or {}))
        if where_clauses:
            query += " AND " + " AND ".join(where_clauses)

        query += " ORDER BY d.reg_date DESC, d.id DESC"
        if limit:
            query += " LIMIT ?"
            params.append(int(limit))

        return self.execute_query(query, params)

    def get_documents_for_period(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query = """
            SELECT d.id
            FROM documents d
            WHERE 1=1
        """
        params: List[Any] = []

        if date_from and date_to:
            query += " AND date(d.reg_date) BETWEEN date(?) AND date(?)"
            params.extend([date_from, date_to])
        elif date_from:
            query += " AND date(d.reg_date) >= date(?)"
            params.append(date_from)
        elif date_to:
            query += " AND date(d.reg_date) <= date(?)"
            params.append(date_to)

        query += " ORDER BY d.reg_date, d.id"
        rows = self.execute_query(query, params)

        documents: List[Dict[str, Any]] = []
        for row in rows:
            document = self.get_document_by_id(int(row["id"]))
            if document:
                documents.append(document)
        return documents

    def get_ref_items(self, table_name: str) -> List[Dict[str, Any]]:
        return self.execute_query(f"SELECT id, name FROM {table_name} ORDER BY name")

    def add_ref_item(self, table_name: str, name: str) -> Optional[int]:
        return self.execute_update(f"INSERT INTO {table_name} (name) VALUES (?)", (name,))

    def get_simple_reference(self, table_name: str) -> List[Dict[str, Any]]:
        return self.execute_query(f"SELECT id, name FROM {table_name} ORDER BY name")

    def get_reference_id_by_name(self, table_name: str, name: str) -> Optional[int]:
        rows = self.execute_query(f"SELECT id FROM {table_name} WHERE name = ?", (name,))
        return int(rows[0]["id"]) if rows else None

    def add_simple_reference(self, table_name: str, name: str) -> Optional[int]:
        return self.execute_update(f"INSERT INTO {table_name} (name) VALUES (?)", (name,))

    def update_simple_reference(self, table_name: str, item_id: int, name: str):
        self.execute_update(f"UPDATE {table_name} SET name = ? WHERE id = ?", (name, item_id))

    def delete_simple_reference(
        self,
        table_name: str,
        item_id: int,
        foreign_key_field: Optional[str] = None,
        check_usage: bool = True,
    ):
        if check_usage and foreign_key_field:
            rows = self.execute_query(
                f"SELECT COUNT(*) AS usage_count FROM documents WHERE {foreign_key_field} = ?",
                (item_id,),
            )
            usage_count = int(rows[0]["usage_count"]) if rows else 0
            if usage_count > 0:
                raise Exception(f"Element is used in {usage_count} documents")
        self.execute_update(f"DELETE FROM {table_name} WHERE id = ?", (item_id,))

    def _list_reference_with_active(self, table_name: str, active_only: bool = False) -> List[Dict[str, Any]]:
        where_clause = "WHERE is_active = 1" if active_only else ""
        return self.execute_query(f"SELECT * FROM {table_name} {where_clause} ORDER BY name")

    def get_executors(self, active_only: bool = True) -> List[Dict[str, Any]]:
        return self._list_reference_with_active("ref_executors", active_only=active_only)

    def add_executor(self, name: str, position: str = "", department: str = "", is_active: bool = True) -> Optional[int]:
        return self.execute_update(
            "INSERT INTO ref_executors (name, position, department, is_active) VALUES (?, ?, ?, ?)",
            (name, position, department, 1 if is_active else 0),
        )

    def update_executor(self, executor_id: int, name: str, position: str, department: str, is_active: bool):
        self.execute_update(
            """
            UPDATE ref_executors
            SET name = ?, position = ?, department = ?, is_active = ?
            WHERE id = ?
            """,
            (name, position, department, 1 if is_active else 0, executor_id),
        )

    def deactivate_executor(self, executor_id: int):
        self.execute_update("UPDATE ref_executors SET is_active = 0 WHERE id = ?", (executor_id,))

    def get_themes(self, active_only: bool = True) -> List[Dict[str, Any]]:
        return self._list_reference_with_active("ref_themes", active_only=active_only)

    def add_theme(self, name: str, description: str = "", is_active: bool = True) -> Optional[int]:
        return self.execute_update(
            "INSERT INTO ref_themes (name, description, is_active) VALUES (?, ?, ?)",
            (name, description, 1 if is_active else 0),
        )

    def update_theme(self, theme_id: int, name: str, description: str, is_active: bool):
        self.execute_update(
            "UPDATE ref_themes SET name = ?, description = ?, is_active = ? WHERE id = ?",
            (name, description, 1 if is_active else 0, theme_id),
        )

    def deactivate_theme(self, theme_id: int):
        self.execute_update("UPDATE ref_themes SET is_active = 0 WHERE id = ?", (theme_id,))

    def get_responsible_executors(self, active_only: bool = True) -> List[Dict[str, Any]]:
        return self._list_reference_with_active("ref_responsible_executors", active_only=active_only)

    def add_responsible_executor(self, name: str, is_active: bool = True) -> Optional[int]:
        return self.execute_update(
            "INSERT INTO ref_responsible_executors (name, is_active) VALUES (?, ?)",
            (name, 1 if is_active else 0),
        )

    def update_responsible_executor(self, executor_id: int, name: str, is_active: bool):
        self.execute_update(
            "UPDATE ref_responsible_executors SET name = ?, is_active = ? WHERE id = ?",
            (name, 1 if is_active else 0, executor_id),
        )

    def deactivate_responsible_executor(self, executor_id: int):
        self.execute_update("UPDATE ref_responsible_executors SET is_active = 0 WHERE id = ?", (executor_id,))

    def get_published_where(self) -> List[Dict[str, Any]]:
        return self.execute_query("SELECT * FROM ref_published_where ORDER BY name")

    def add_published_where(self, name: str) -> Optional[int]:
        return self.execute_update("INSERT INTO ref_published_where (name) VALUES (?)", (name,))

    def update_published_where(self, item_id: int, name: str):
        self.execute_update("UPDATE ref_published_where SET name = ? WHERE id = ?", (name, item_id))

    def delete_published_where(self, item_id: int):
        rows = self.execute_query(
            "SELECT COUNT(*) AS usage_count FROM documents WHERE published_where_id = ?",
            (item_id,),
        )
        usage_count = int(rows[0]["usage_count"]) if rows else 0
        if usage_count > 0:
            raise Exception(f"Publication place is used in {usage_count} documents")
        self.execute_update("DELETE FROM ref_published_where WHERE id = ?", (item_id,))

    def get_document_signers(self, document_id: int) -> List[Dict[str, Any]]:
        return self.execute_query(
            """
            SELECT rs.id, rs.name
            FROM document_signers ds
            JOIN ref_signers rs ON ds.signer_id = rs.id
            WHERE ds.document_id = ?
            ORDER BY rs.name
            """,
            (document_id,),
        )

    def get_document_approvers(self, document_id: int) -> List[Dict[str, Any]]:
        return self.execute_query(
            """
            SELECT ra.id, ra.name
            FROM document_approvers da
            JOIN ref_approvers ra ON da.approver_id = ra.id
            WHERE da.document_id = ?
            ORDER BY ra.name
            """,
            (document_id,),
        )

    def update_document_signers(self, document_id: int, signer_ids: List[int]):
        with self.transaction() as cursor:
            cursor.raw_cursor.execute("DELETE FROM document_signers WHERE document_id = ?", (document_id,))
            self._insert_document_relations(cursor, "document_signers", document_id, "signer_id", signer_ids)

    def update_document_approvers(self, document_id: int, approver_ids: List[int]):
        with self.transaction() as cursor:
            cursor.raw_cursor.execute("DELETE FROM document_approvers WHERE document_id = ?", (document_id,))
            self._insert_document_relations(cursor, "document_approvers", document_id, "approver_id", approver_ids)
