import sqlite3
from typing import List, Dict, Optional, Any
from datetime import datetime
import os
import ctypes
import time
import functools
import threading
from contextlib import contextmanager
import app_config
from pathlib import Path, PurePosixPath
from database_common import DatabaseLockError, DocumentConflictError


def retry_on_busy(max_attempts=5, delay=0.5):
    """
    Декоратор для автоматических повторных попыток при блокировке БД
    
    Args:
        max_attempts: Максимальное количество попыток (по умолчанию 5)
        delay: Начальная задержка между попытками в секундах
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    error_msg = str(e).lower()
                    is_locked = any(keyword in error_msg for keyword in [
                        'database is locked', 'locked', 'busy'
                    ])
                    
                    if is_locked:
                        if attempt < max_attempts - 1:
                            wait_time = delay * (2 ** attempt)  # Экспоненциальная задержка
                            print(f"⏳ БД заблокирована, попытка {attempt + 1}/{max_attempts}, ожидание {wait_time:.1f}с...")
                            time.sleep(wait_time)
                            continue
                        else:
                            print(f"❌ БД заблокирована после {max_attempts} попыток")
                            raise DatabaseLockError(
                                f"База данных заблокирована после {max_attempts} попыток. "
                                f"Пожалуйста, повторите попытку позже."
                            )
                    else:
                        raise
            return None
        return wrapper
    return decorator
class DatabaseManager:
    """Оптимизированный менеджер базы данных для работы с 105k+ документов"""
    backend = "sqlite"
    _connection_lock = threading.Lock()

    @staticmethod
    def _is_network_path(path: str) -> bool:
        """Определить, находится ли путь на сетевом ресурсе."""
        abs_path = os.path.abspath(path)
        if abs_path.startswith("\\\\"):
            return True

        drive, _ = os.path.splitdrive(abs_path)
        if os.name == "nt" and drive:
            try:
                # DRIVE_REMOTE = 4
                drive_type = ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\")
                return drive_type == 4
            except Exception:
                return False
        return False

    def __init__(self, db_path: str, create_if_not_exists: bool = False):
        self.db_path = db_path
        self.conn = None
        self.cursor = None
        self._db_lock = threading.RLock()
        self.is_network_db = self._is_network_path(db_path)
        self.expected_journal_mode = "DELETE" if self.is_network_db else "WAL"
        self.expected_synchronous = "FULL" if self.is_network_db else "NORMAL"
        # 🆕 ДОБАВИТЬ: Извлекаем путь к папке БД
        self.db_folder = os.path.dirname(os.path.abspath(db_path))
        # Ожидаем структуру: data/(название БД)/database.db
        # Поэтому files будут в: data/(название БД)/files/
        self.files_base_path = os.path.join(self.db_folder, "files")
        self.local_files_root = Path(self.db_folder) / "files"
        self.local_files_root.mkdir(parents=True, exist_ok=True)
        env_files_root = os.getenv("ACTI_FILES_ROOT", "").strip()
        if env_files_root:
            candidate_root = Path(env_files_root)
            if candidate_root.exists():
                self.files_root = candidate_root
            else:
                print(f"[files-root] ACTI_FILES_ROOT unavailable: {env_files_root}. Using local files root.")
                self.files_root = self.local_files_root
        else:
            self.files_root = self.local_files_root
        print(f"[files-root] active root: {self.files_root}")
        # 🆕 ДОБАВИТЬ: Создаем папку files если её нет
        if not os.path.exists(self.files_base_path):
            os.makedirs(self.files_base_path, exist_ok=True)
            print(f"[files-root] created local folder: {self.files_base_path}")
        self.connect()
        print("Подключение к БД", create_if_not_exists)
        if create_if_not_exists:
            self.create_tables_if_not_exist() # Создание таблиц если их нет
        self.optimize_for_large_dataset()
    @contextmanager
    def transaction(self, immediate=False):
        """
        Контекстный менеджер для безопасных транзакций
        
        Args:
            immediate: Если True, использует BEGIN IMMEDIATE (для записи)
        """
        with self._db_lock:
            tx_cursor = self.conn.cursor()
            try:
                if immediate:
                    tx_cursor.execute("BEGIN IMMEDIATE")
                else:
                    tx_cursor.execute("BEGIN")

                yield tx_cursor
                self.conn.commit()

            except Exception as e:
                self.conn.rollback()
                print(f"❌ Ошибка транзакции: {e}")
                raise
    def get_files_path(self, year: int, month: int) -> str:
        """
        Получить путь к папке для файлов по году и месяцу
        
        Args:
            year: Год регистрации документа
            month: Месяц регистрации документа
        
        Returns:
            str: Полный путь к папке (например: data/Основная/files/2025/10/)
        """
        # Формируем путь: data/(название БД)/files/(год)/(месяц)/
        year_folder = os.path.join(self.files_base_path, str(year))
        month_folder = os.path.join(year_folder, str(month))
        
        # Создаем папки если их нет
        os.makedirs(year_folder, exist_ok=True)
        os.makedirs(month_folder, exist_ok=True)
        
        return month_folder
    

    def get_full_file_path(self, relative_path: str) -> str:
        """
        relative_path: как в БД, например 'files\\2010\\01\\3.doc' или 'files/2010/01/3.doc'
        Возвращает полный путь от выбранного files_root (UNC приоритетнее, иначе локальный).
        """
        if not relative_path:
            raise ValueError("Пустой путь документа из БД")

        # Если путь уже абсолютный - возвращаем как есть
        # (При желании можно запретить абсолютные пути политикой)
        if os.path.isabs(relative_path):
            return relative_path

        # Нормализуем слеши
        rel = str(PurePosixPath(relative_path.replace("\\", "/")))

        # Защита от выхода из корня и от "абсолютности" в БД
        p = PurePosixPath(rel)
        if ".." in p.parts or rel.startswith(("/", "\\")) or ":" in rel:
            raise ValueError(f"Некорректный путь в БД: {relative_path}")

        # В БД у вас уже есть префикс 'files/...', а files_root уже указывает на .../files
        if rel.startswith("files/"):
            rel = rel[len("files/"):]

        full = Path(self.files_root) / Path(*PurePosixPath(rel).parts)
        return str(full)

    def make_relative_document_path(self, absolute_path: str) -> str:
        full_path = Path(absolute_path).resolve()
        try:
            relative = full_path.relative_to(Path(self.files_root).resolve())
            return f"files/{str(relative).replace('\\', '/')}"
        except Exception:
            return os.path.relpath(str(full_path), self.db_folder).replace("\\", "/")
    

    def create_tables_if_not_exist(self):
        "Создание всех таблиц БД если их нет"
        print("Создаю базу данных")
        try:
            cursor = self.conn.cursor()
            
            # 1. Основная таблица documents
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reg_number TEXT,
                    reg_date TEXT,
                    number TEXT,
                    status_id INTEGER,
                    type_id INTEGER,
                    signing_type_id INTEGER,
                    document_kind_id INTEGER,
                    theme_id INTEGER,
                    executor_id INTEGER,
                    responsible_executor_id INTEGER,
                    title TEXT,
                    document_path TEXT,
                    should_publish TEXT,
                    published_where_id INTEGER,
                    published_date TEXT,
                    control_date TEXT,
                    removed_from_control TEXT,
                    execution_result TEXT,
                    pages_count INTEGER,
                    attachments_count INTEGER,
                    case_number TEXT,
                    volume_number TEXT,
                    sheets TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 2. Справочник статусов
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ref_status (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL
                )
            """)
            
            # 3. Справочник типов документов
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ref_document_types (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL
                )
            """)
            
            # 4. Справочник типов подписания
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ref_signing_types (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL
                )
            """)
            
            # 5. Справочник видов документов
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ref_document_kinds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL
                )
            """)
            
            # 6. Справочник мест публикации
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ref_published_where (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL
                )
            """)
            
            # 7. Справочник исполнителей (С ПОЛЯМИ position и department!)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ref_executors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    position TEXT,
                    department TEXT,
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 8. Справочник ответственных исполнителей
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ref_responsible_executors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    is_active INTEGER DEFAULT 1
                )
            """)
            
            # 9. Справочник тем (С ПОЛЕМ is_active!)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ref_themes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT,
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 10. Справочник подписантов
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ref_signers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL
                )
            """)
            
            # 11. Справочник согласующих
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ref_approvers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL
                )"""
                           )
            
            
            # 12. Связь документов с подписантами
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS document_signers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    signer_id INTEGER NOT NULL,
                    UNIQUE(document_id, signer_id),
                    FOREIGN KEY (document_id) REFERENCES documents(id),
                    FOREIGN KEY (signer_id) REFERENCES ref_signers(id)
                )
            """)
            
            # 13. Связь документов с согласующими
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS document_approvers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    approver_id INTEGER,
                    UNIQUE(document_id, approver_id),
                    FOREIGN KEY (document_id) REFERENCES documents(id),
                    FOREIGN KEY (approver_id) REFERENCES ref_approvers(id)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS db_metadata (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    last_backup_date TEXT,
                    backup_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # ✅ КРИТИЧНО: Инициализируем первую запись
            cursor.execute("""
                INSERT OR IGNORE INTO db_metadata (id) VALUES (1)
            """)
            
            # Создаем индексы для производительности
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_docs_status ON documents(status_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_docs_type ON documents(type_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_docs_executor ON documents(executor_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_docs_theme ON documents(theme_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_docs_reg_date ON documents(reg_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_docs_reg_number ON documents(reg_number)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_doc_signers_doc ON document_signers(document_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_doc_signers_signer ON document_signers(signer_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_doc_approvers_doc ON document_approvers(document_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_doc_approvers_approver ON document_approvers(approver_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_docs_reg_date_id ON documents(reg_date DESC, id DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_docs_title ON documents(title COLLATE NOCASE)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_docs_reg_number ON documents(reg_number)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_docs_date_components ON documents(strftime('%Y', reg_date), strftime('%m', reg_date))")
            # Добавляем базовые данные в справочники
            self._insert_default_reference_data(cursor)
            
            self.conn.commit()
            print("✅ Все таблицы созданы успешно")
            
        except Exception as e:
            print(f"❌ Ошибка создания таблиц: {e}")
            raise

    def _insert_default_reference_data(self, cursor):
        "Добавить базовые данные в справочники"
        try:
            # Статусы
            statuses = ['Внесены дополнения', 'Внесены дополнения и изменения', 'Внесены изменения', 'Действует','Утратило силу','Отменено']
            for status in statuses:
                cursor.execute("INSERT OR IGNORE INTO ref_status (name) VALUES (?)", (status,))
            
            # Типы документов
            types = ['Постановление', 'Распоряжение']
            for doc_type in types:
                cursor.execute("INSERT OR IGNORE INTO ref_document_types (name) VALUES (?)", (doc_type,))
            
            # Типы подписания
            signing_types = ['Одностороннее', 'Многостороннее']
            for s_type in signing_types:
                cursor.execute("INSERT OR IGNORE INTO ref_signing_types (name) VALUES (?)", (s_type,))
            
            # Виды документов
            kinds = ['Ненормативный правовой акт', 'Нормативный правовой акт']
            for kind in kinds:
                cursor.execute("INSERT OR IGNORE INTO ref_document_kinds (name) VALUES (?)", (kind,))
            
            # Места публикации
            published = ['"Сосновская Нива"', 'Информационный бюллетень \"Сосновская Нива\"']
            for place in published:
                cursor.execute("INSERT OR IGNORE INTO ref_published_where (name) VALUES (?)", (place,))
            
            print("✅ Базовые данные добавлены в справочники")
            
        except Exception as e:
            print(f"⚠️ Предупреждение при добавлении базовых данных: {e}")
    def get_last_backup_date(self) -> Optional[str]:
        """
        Получить дату последней архивации
        
        Returns:
            Optional[str]: Дата в формате ISO (YYYY-MM-DD HH:MM:SS) или None
        """
        try:
            query = "SELECT last_backup_date FROM db_metadata WHERE id = 1"
            self.cursor.execute(query)
            result = self.cursor.fetchone()
            
            if result and result[0]:
                return result[0]
            return None
            
        except Exception as e:
            print(f"❌ Ошибка получения даты архивации: {e}")
            return None


    def update_backup_date(self):
        """
        Обновить дату последней архивации (текущее время)
        
        Returns:
            bool: True если успешно, False при ошибке
        """
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # ✅ ИСПРАВЛЕНИЕ: Проверяем существование записи
            self.cursor.execute("SELECT COUNT(*) FROM db_metadata WHERE id = 1")
            
            if self.cursor.fetchone()[0] == 0:
                # Записи нет - создаем с датой
                print("⚠️ Создаем первую запись в db_metadata...")
                query = """
                    INSERT INTO db_metadata (id, last_backup_date, backup_count) 
                    VALUES (1, ?, 1)
                """
                self.cursor.execute(query, (current_time,))
            else:
                # Запись есть - обновляем
                query = """
                    UPDATE db_metadata 
                    SET last_backup_date = ?,
                        backup_count = backup_count + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = 1
                """
                self.cursor.execute(query, (current_time,))
            
            self.conn.commit()
            
            print(f"✅ Дата архивации обновлена: {current_time}")
            return True
            
        except Exception as e:
            print(f"❌ Ошибка обновления даты архивации: {e}")
            import traceback
            traceback.print_exc()
            self.conn.rollback()
            return False

    @retry_on_busy(max_attempts=5, delay=0.5)
    def get_backup_statistics(self) -> Dict[str, Any]:
        """
        Получить статистику архивирования
        
        Returns:
            Dict: {'last_backup': str, 'backup_count': int, 'days_since_backup': int}
        """
        try:
            # ✅ ИСПРАВЛЕНИЕ: Проверяем существование таблицы
            self.cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='db_metadata'
            """)
            
            if not self.cursor.fetchone():
                # Таблица не существует - создаем
                print("⚠️ Таблица db_metadata не найдена, создаем...")
                self._create_metadata_table()
            
            # ✅ ИСПРАВЛЕНИЕ: Проверяем наличие записи
            self.cursor.execute("SELECT COUNT(*) FROM db_metadata WHERE id = 1")
            if self.cursor.fetchone()[0] == 0:
                # Записи нет - создаем
                print("⚠️ Запись в db_metadata не найдена, создаем...")
                self.cursor.execute("INSERT INTO db_metadata (id) VALUES (1)")
                self.conn.commit()
            
            # Теперь получаем данные
            query = """
                SELECT 
                    last_backup_date,
                    backup_count,
                    CAST((julianday('now') - julianday(last_backup_date)) AS INTEGER) as days_since
                FROM db_metadata 
                WHERE id = 1
            """
            
            self.cursor.execute(query)
            result = self.cursor.fetchone()
            
            if result:
                return {
                    'last_backup': result[0],
                    'backup_count': result[1] or 0,
                    'days_since_backup': result[2] if result[0] else None
                }
            
            # Fallback на безопасные значения
            return {
                'last_backup': None,
                'backup_count': 0,
                'days_since_backup': None
            }
            
        except Exception as e:
            print(f"❌ Ошибка получения статистики архивирования: {e}")
            import traceback
            traceback.print_exc()
            
            # Возвращаем безопасные значения при ошибке
            return {
                'last_backup': None,
                'backup_count': 0,
                'days_since_backup': None
            }
    def _create_metadata_table(self):
        """
        Создать таблицу метаданных если её нет
        
        ⚠️ Вспомогательный метод для обратной совместимости со старыми БД
        """
        try:
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS db_metadata (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    last_backup_date TEXT,
                    backup_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            self.cursor.execute("""
                INSERT OR IGNORE INTO db_metadata (id) VALUES (1)
            """)
            
            self.conn.commit()
            print("✅ Таблица db_metadata создана")
            
        except Exception as e:
            print(f"❌ Ошибка создания таблицы db_metadata: {e}")
            self.conn.rollback()
            raise
    def connect(self):
        """Установить соединение с оптимизациями для многопользовательского режима"""
        try:
            profile = "NETWORK_SAFE" if self.is_network_db else "LOCAL_FAST"
            print(
                f"ℹ️ Профиль SQLite: {profile} "
                f"(journal={self.expected_journal_mode}, synchronous={self.expected_synchronous})"
            )
            
            self.conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,  # Разрешаем доступ из разных потоков
                timeout=60.0,  # ⬆️ УВЕЛИЧЕН: 60 секунд вместо 30
                isolation_level=None  # ⚡ Автокоммит выключен - управляем транзакциями вручную
            )
            
            self.conn.row_factory = sqlite3.Row
            self.cursor = self.conn.cursor()
            
            # Базовые настройки соединения.
            self.cursor.execute("PRAGMA busy_timeout = 60000")
            self.cursor.execute("PRAGMA foreign_keys = ON")

            # Настройки надежности/производительности зависят от типа хранилища.
            self.cursor.execute(f"PRAGMA journal_mode={self.expected_journal_mode}")
            self.cursor.execute(f"PRAGMA synchronous={self.expected_synchronous}")

            if self.is_network_db:
                # Для сетевой шары приоритет надежности.
                self.cursor.execute("PRAGMA cache_size = -50000")
                self.cursor.execute("PRAGMA temp_store = FILE")
            else:
                # Для локального диска можно ускорить работу через WAL.
                self.cursor.execute("PRAGMA cache_size = -100000")
                self.cursor.execute("PRAGMA temp_store = MEMORY")
                self.cursor.execute("PRAGMA wal_autocheckpoint = 1000")
            
            print(f"✅ Подключение к БД: {self.db_path}")
            
        except Exception as e:
            print(f"❌ Ошибка подключения к БД: {e}")
            raise
    
    def optimize_for_large_dataset(self):
        """Оптимизация БД для работы с большими данными И многопользовательского режима"""
        try:
            # Проверяем режим журналирования
            result = self.cursor.execute("PRAGMA journal_mode").fetchone()
            current_mode = result[0] if result else "unknown"
            expected_mode = self.expected_journal_mode.lower()
            
            if current_mode.lower() != expected_mode:
                print(f"⚠️  Режим журналирования: {current_mode} (переключаем на {self.expected_journal_mode})")
                self.cursor.execute(f"PRAGMA journal_mode={self.expected_journal_mode}")
                print(f"✅ Переключено на {self.expected_journal_mode} режим")
            else:
                print(f"✅ Режим журналирования: {self.expected_journal_mode}")
            
            # Оптимизация индексов
            self.cursor.execute("PRAGMA optimize")
            
            print("✅ БД оптимизирована для больших данных и многопользовательского режима")
            
        except Exception as e:
            print(f"⚠️  Предупреждение при оптимизации: {e}")
    
    def close(self):
        """Закрыть соединение с базой данных"""
        if self.conn:
            try:
                # Финальный checkpoint нужен только для WAL.
                if self.expected_journal_mode.upper() == "WAL":
                    self.cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self.conn.close()
                print("[sqlite] connection closed")
            except Exception as e:
                print(f"[sqlite] warning while closing connection: {e}")
    
    # ==================== РАБОТА С ДОКУМЕНТАМИ ====================
    @retry_on_busy(max_attempts=5, delay=0.5)
    def add_document(self, data: Dict[str, Any]) -> int:
        """Добавить документ в БД"""
        try:
            with self.transaction(immediate=True):
                # Основные поля
                fields = {
                    'reg_number': data.get('reg_number', ''),
                    'reg_date': data.get('reg_date'),
                    'number': data.get('number', ''),
                    'status_id': data.get('status_id'),
                    'type_id': data.get('type_id'),
                    'signing_type_id': data.get('signing_type_id'),
                    'document_kind_id': data.get('document_kind_id'),
                    'theme_id': data.get('theme_id'),
                    'executor_id': data.get('executor_id'),
                    'responsible_executor_id': data.get('responsible_executor_id'),
                    'title': data.get('title', ''),
                    'document_path': data.get('document_path', ''),
                    'should_publish': data.get('should_publish', ''),
                    'published_where_id': data.get('published_where_id'),
                    'published_date': data.get('published_date'),
                    'control_date': data.get('control_date'),
                    'removed_from_control': data.get('removed_from_control', ''),
                    'execution_result': data.get('execution_result', ''),
                    'pages_count': data.get('pages_count'),
                    'attachments_count': data.get('attachments_count'),
                    'case_number': data.get('case_number', ''),
                    'volume_number': data.get('volume_number', ''),
                    'sheets': data.get('sheets', '')
                }
                
                # Формируем запрос
                columns = ', '.join(fields.keys())
                placeholders = ', '.join(['?' for _ in fields])
                query = f"INSERT INTO documents ({columns}) VALUES ({placeholders})"
                
                self.cursor.execute(query, tuple(fields.values()))
                
                
                doc_id = self.cursor.lastrowid
                
                # Добавляем подписантов если есть
                if 'signers' in data and data['signers']:
                    self._add_document_signers(doc_id, data['signers'])
                
                # Добавляем согласующих если есть
                if 'approvers' in data and data['approvers']:
                    self._add_document_approvers(doc_id, data['approvers'])
                
            print(f"✅ Документ добавлен с ID: {doc_id}")
            return doc_id
        except DatabaseLockError:
            print(f"❌ Не удалось добавить документ: БД заблокирована")
            raise    
        except Exception as e:
            print(f"❌ Ошибка добавления документа: {e}")
            raise
    def search_documents_with_filters(self, filters):
        try:
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
            if filters.get('search_text'):
                query += " AND (d.title LIKE ? OR d.reg_number LIKE ?)"
                search_term = f"%{filters['search_text']}%"
                params.extend([search_term, search_term])
            if filters.get('status'):
                query += " AND LOWER(s.name) LIKE ?"
                params.append(f"%{filters['status']}%")
            if filters.get('year'):
                query += " AND strftime('%Y', d.reg_date) = ?"
                params.append(str(filters['year']))
            if filters.get('month'):
                query += " AND CAST(strftime('%m', d.reg_date) AS INTEGER) = ?"
                params.append(filters['month'])
            if filters.get('date_from') and filters['date_from']:
                query += " AND date(d.reg_date) >= date(?)"
                params.append(filters['date_from'])
            if filters.get('date_to') and filters['date_to']:
                query += " AND date(d.reg_date) <= date(?)"
                params.append(filters['date_to'])
            query += " ORDER BY d.reg_date DESC, d.id DESC LIMIT 1000"
            cursor = self.connection.cursor()
            cursor.execute(query, params)
            documents = cursor.fetchall()
            result = []
            for doc in documents:
                result.append({
                    'id': doc[0], 'title': doc[1], 'reg_number': doc[2],
                    'reg_date': doc[3], 'status': doc[4], 'type_doc': doc[5],
                    'filename': doc[6], 'executor_name': doc[7], 'theme_name': doc[8]
                })
            return result
        except Exception as e:
            print(f"❌ Ошибка поиска: {e}")
            import traceback
            traceback.print_exc()
            return []

    @staticmethod
    def _normalize_id_list(ids: Optional[List[Any]]) -> List[int]:
        normalized: List[int] = []
        for raw in ids or []:
            if raw is None:
                continue
            normalized.append(int(raw))
        return sorted(set(normalized))

    @staticmethod
    def _normalize_snapshot_value(value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    def _get_updatable_document_fields(self) -> List[str]:
        return [
            'reg_number', 'reg_date', 'number', 'status_id', 'type_id',
            'signing_type_id', 'document_kind_id', 'theme_id', 'executor_id',
            'responsible_executor_id', 'title', 'document_path', 'should_publish',
            'published_where_id', 'published_date', 'control_date',
            'removed_from_control', 'execution_result', 'pages_count',
            'attachments_count', 'case_number', 'volume_number', 'sheets'
        ]

    def _get_document_edit_snapshot_with_cursor(self, cursor, document_id: int) -> Dict[str, Any]:
        fields = self._get_updatable_document_fields()
        query = f"SELECT {', '.join(fields)} FROM documents WHERE id = ?"
        cursor.execute(query, (document_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Документ с ID {document_id} не найден")

        snapshot = {field: self._normalize_snapshot_value(row[field]) for field in fields}

        cursor.execute("SELECT signer_id FROM document_signers WHERE document_id = ?", (document_id,))
        snapshot["signers"] = self._normalize_id_list([r[0] for r in cursor.fetchall()])

        cursor.execute("SELECT approver_id FROM document_approvers WHERE document_id = ?", (document_id,))
        snapshot["approvers"] = self._normalize_id_list([r[0] for r in cursor.fetchall()])
        return snapshot

    def get_document_edit_snapshot(self, document_id: int) -> Dict[str, Any]:
        with self._db_lock:
            cursor = self.conn.cursor()
            return self._get_document_edit_snapshot_with_cursor(cursor, document_id)

    def _assert_no_snapshot_conflict(self, cursor, document_id: int, expected_snapshot: Dict[str, Any]):
        current_snapshot = self._get_document_edit_snapshot_with_cursor(cursor, document_id)
        normalized_expected: Dict[str, Any] = {}
        for key, value in (expected_snapshot or {}).items():
            if key in ("signers", "approvers"):
                normalized_expected[key] = self._normalize_id_list(value)
            else:
                normalized_expected[key] = self._normalize_snapshot_value(value)

        if current_snapshot != normalized_expected:
            raise DocumentConflictError(
                "Документ уже был изменен другим пользователем.",
                current_snapshot=current_snapshot
            )

    @retry_on_busy(max_attempts=5, delay=0.5)
    def update_document(
        self,
        document_id: int,
        data: Dict[str, Any],
        expected_snapshot: Optional[Dict[str, Any]] = None,
        force_overwrite: bool = False
    ):
        """Обновить документ"""
        try:
            with self.transaction(immediate=True) as cursor:
                if expected_snapshot and not force_overwrite:
                    self._assert_no_snapshot_conflict(cursor, document_id, expected_snapshot)

            # Обновляем основные поля
                set_parts = []
                values = []
                
                updatable_fields = self._get_updatable_document_fields()
                
                for field in updatable_fields:
                    if field in data:
                        set_parts.append(f"{field} = ?")
                        values.append(data[field])
                
                if set_parts:
                    query = f"UPDATE documents SET {', '.join(set_parts)} WHERE id = ?"
                    values.append(document_id)
                    cursor.execute(query, values)
                
                # Обновляем подписантов если есть
                if 'signers' in data:
                    self._update_document_signers(document_id, data['signers'])
                
                # Обновляем согласующих если есть
                if 'approvers' in data:
                    self._update_document_approvers(document_id, data['approvers'])
                
                
            print(f"✅ Документ ID {document_id} обновлен")
        except DatabaseLockError:
            print(f"❌ Не удалось добавить документ: БД заблокирована")
            raise    
        except Exception as e:
            print(f"❌ Ошибка обновления документа: {e}")
            
            raise
    # === МЕТОДЫ ДЛЯ СПРАВОЧНИКОВ ===
    
    def get_executors(self, active_only: bool = True) -> List[Dict]:
        """Получить список исполнителей"""
        try:
            where_clause = "WHERE is_active = 1" if active_only else ""
            query = f"SELECT * FROM ref_executors {where_clause} ORDER BY name"
            self.cursor.execute(query)
            return [dict(row) for row in self.cursor.fetchall()]
        except Exception as e:
            print(f"❌ Ошибка получения исполнителей: {e}")
            return []
    @retry_on_busy(max_attempts=5, delay=0.5)
    def add_executor(self, name: str, position: str = "", department: str = "",is_active: bool = True) -> Optional[int]:
        "Добавить исполнителя"
        try:
            with self.transaction(immediate=True): 
                query = """INSERT INTO ref_executors (name, position, department, is_active) VALUES (?, ?, ?, ?)"""
                self.cursor.execute(query, (name, position, department, 1 if is_active else 0))
                
            return self.cursor.lastrowid
        except DatabaseLockError:
            print(f"❌ Не удалось добавить документ: БД заблокирована")
            raise
        except Exception as e:
            print(f"❌ Ошибка добавления исполнителя: {e}")
            return None

    def get_themes(self, active_only: bool = True) -> List[Dict]:
        """Получить список тем"""
        try:
            where_clause = "WHERE is_active = 1" if active_only else ""
            query = f"SELECT * FROM ref_themes {where_clause} ORDER BY name"
            self.cursor.execute(query)
            return [dict(row) for row in self.cursor.fetchall()]
        except Exception as e:
            print(f"❌ Ошибка получения тем: {e}")
            return []
    def get_responsible_executors(self, active_only: bool = True) -> List[Dict]:
        """Получить список ответственных исполнителей"""
        try:
            where_clause = "WHERE is_active = 1" if active_only else ""
            query = f"SELECT * FROM ref_responsible_executors {where_clause} ORDER BY name"
            self.cursor.execute(query)
            return [dict(row) for row in self.cursor.fetchall()]
        except Exception as e:
            print(f"❌ Ошибка получения ответственных исполнителей: {e}")
            return []
    @retry_on_busy(max_attempts=5, delay=0.5)
    def add_responsible_executor(self, name: str, is_active: bool = True) -> Optional[int]:
        """Добавить ответственного исполнителя"""
        try:
            with self.transaction(immediate=True):
                query = """INSERT INTO ref_responsible_executors (name, is_active) 
                        VALUES (?, ?)"""
                self.cursor.execute(query, (name, 1 if is_active else 0))
                
            return self.cursor.lastrowid
        except DatabaseLockError:
            print(f"❌ Не удалось добавить документ: БД заблокирована")
            raise
        except Exception as e:
            print(f"❌ Ошибка добавления ответственного исполнителя: {e}")
            return None
    def get_published_where(self) -> List[Dict]:
        """Получить список мест публикации"""
        try:
            query = "SELECT * FROM ref_published_where ORDER BY name"
            self.cursor.execute(query)
            return [dict(row) for row in self.cursor.fetchall()]
        except Exception as e:
            print(f"❌ Ошибка получения мест публикации: {e}")
            return []
    @retry_on_busy(max_attempts=5, delay=0.5)
    def add_published_where(self, name: str) -> Optional[int]:
        """Добавить место публикации"""
        try:
            with self.transaction(immediate=True):
                query = "INSERT INTO ref_published_where (name) VALUES (?)"
                self.cursor.execute(query, (name,))
                
            return self.cursor.lastrowid
        except DatabaseLockError:
            print(f"❌ Не удалось добавить документ: БД заблокирована")
            raise
        except Exception as e:
            print(f"❌ Ошибка добавления места публикации: {e}")
            return None
    @retry_on_busy(max_attempts=5, delay=0.5)
    def update_published_where(self, item_id: int, name: str):
        """Обновить место публикации"""
        try:
            with self.transaction(immediate=True):
                query = "UPDATE ref_published_where SET name = ? WHERE id = ?"
                self.cursor.execute(query, (name, item_id))
        except DatabaseLockError:
            print(f"❌ Не удалось добавить документ: БД заблокирована")
            raise    
        except Exception as e:
            print(f"❌ Ошибка обновления места публикации: {e}")
            raise
    @retry_on_busy(max_attempts=5, delay=0.5)        
    def delete_published_where(self, item_id: int):
        """Удалить место публикации"""
        try:
            with self.transaction(immediate=True):
                # Проверяем, используется ли
                query = "SELECT COUNT(*) FROM documents WHERE published_where_id = ?"
                self.cursor.execute(query, (item_id,))
                count = self.cursor.fetchone()[0]
                
                if count > 0:
                    raise Exception(f"Место публикации используется в {count} документах")
                
                query = "DELETE FROM ref_published_where WHERE id = ?"
                self.cursor.execute(query, (item_id,))
        except DatabaseLockError:
            print(f"❌ Не удалось добавить документ: БД заблокирована")
            raise    
        except Exception as e:
            print(f"❌ Ошибка удаления места публикации: {e}")
            raise
    @retry_on_busy(max_attempts=5, delay=0.5)
    def update_responsible_executor(self, executor_id: int, name: str, is_active: bool):
        """Обновить ответственного исполнителя"""
        try:
            with self.transaction(immediate=True): 
                query = """UPDATE ref_responsible_executors 
                        SET name = ?, is_active = ? 
                        WHERE id = ?"""
                self.cursor.execute(query, (name, 1 if is_active else 0, executor_id))
        except DatabaseLockError:
            print(f"❌ Не удалось добавить документ: БД заблокирована")
            raise        
        except Exception as e:
            print(f"❌ Ошибка обновления ответственного исполнителя: {e}")
            raise
    @retry_on_busy(max_attempts=5, delay=0.5)        
    def deactivate_responsible_executor(self, executor_id: int):
        """Деактивировать ответственного исполнителя"""
        try:
            with self.transaction(immediate=True):
                query = "UPDATE ref_responsible_executors SET is_active = 0 WHERE id = ?"
                self.cursor.execute(query, (executor_id,))
        except DatabaseLockError:
            print(f"❌ Не удалось добавить документ: БД заблокирована")
            raise    
        except Exception as e:
            print(f"❌ Ошибка деактивации ответственного исполнителя: {e}")
    
    @retry_on_busy(max_attempts=5, delay=0.5)
    def add_theme(self, name: str, description: str = "", is_active: bool = True) -> Optional[int]:
        "Добавить тему"
        try:
            with self.transaction(immediate=True):
                query = "INSERT INTO ref_themes (name, description, is_active) VALUES (?, ?, ?)"
                self.cursor.execute(query, (name, description, 1 if is_active else 0))
            
            return self.cursor.lastrowid
        except DatabaseLockError:
            print(f"❌ Не удалось добавить документ: БД заблокирована")
            raise
        except Exception as e:
            print(f"❌ Ошибка добавления темы: {e}")
            return None
    @retry_on_busy(max_attempts=5, delay=0.5)
    def deactivate_executor(self, executor_id: int):

        """Деактивировать исполнителя (не удаляем, а помечаем неактивным)"""
        try:
            with self.transaction(immediate=True):
                query = "UPDATE ref_executors SET is_active = 0 WHERE id = ?"
                self.cursor.execute(query, (executor_id,))
        except DatabaseLockError:
            print(f"❌ Не удалось добавить документ: БД заблокирована")
            raise    
        except Exception as e:
            print(f"❌ Ошибка деактивации исполнителя: {e}")
    @retry_on_busy(max_attempts=5, delay=0.5)
    def deactivate_theme(self, theme_id: int):
        """Деактивировать тему"""
        try:
            with self.transaction(immediate=True): 
                query = "UPDATE ref_themes SET is_active = 0 WHERE id = ?"
                self.cursor.execute(query, (theme_id,))
        except DatabaseLockError:
            print(f"❌ Не удалось добавить документ: БД заблокирована")
            raise        
        except Exception as e:
            print(f"❌ Ошибка деактивации темы: {e}")
    
    def get_document_by_id(self, doc_id: int) -> Dict:
        """Получить документ по ID с полной информацией"""
        try:
            query = """
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
            """
            
            with self._db_lock:
                cursor = self.conn.cursor()
                cursor.execute(query, (doc_id,))
                row = cursor.fetchone()
            
            if not row:
                return {}
            
            doc = dict(row)
            
            # Добавляем подписантов
            doc['signers'] = self._get_document_signers(doc_id)
            
            # Добавляем согласующих
            doc['approvers'] = self._get_document_approvers(doc_id)
            
            return doc
            
        except Exception as e:
            print(f"❌ Ошибка получения документа по ID: {e}")
            return {}
    
    def get_documents(self, filters: Dict = None, limit: int = 1000, offset: int = 0) -> List[Dict]:
        """
        Получить список документов с пагинацией
        Оптимизировано для больших данных
        """
        try:
            where_clauses = []
            params = []
            
            if filters:
                if 'status_id' in filters:
                    where_clauses.append("d.status_id = ?")
                    params.append(filters['status_id'])
                
                if 'type_id' in filters:
                    where_clauses.append("d.type_id = ?")
                    params.append(filters['type_id'])
                
                if 'executor_id' in filters:
                    where_clauses.append("d.executor_id = ?")
                    params.append(filters['executor_id'])
                
                if 'theme_id' in filters:
                    where_clauses.append("d.theme_id = ?")
                    params.append(filters['theme_id'])
                
                if 'date_from' in filters:
                    where_clauses.append("date(d.reg_date) >= date(?)")
                    params.append(filters['date_from'])
                
                if 'date_to' in filters:
                    where_clauses.append("date(d.reg_date) <= date(?)")
                    params.append(filters['date_to'])
                
                if 'search_text' in filters:
                    where_clauses.append(
                        "(d.title LIKE ? OR d.reg_number LIKE ? OR d.document_path LIKE ?)"
                    )
                    search_pattern = f"%{filters['search_text']}%"
                    params.extend([search_pattern, search_pattern, search_pattern])
            
            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            
            query = f"""
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
                {where_sql}
                ORDER BY
                    CASE
                        WHEN d.reg_date IS NULL OR TRIM(d.reg_date) = '' THEN 0
                        ELSE 1
                    END ASC,
                    d.reg_date DESC,
                    d.id DESC
                LIMIT ? OFFSET ?
            """
            
            params.extend([limit, offset])
            
            self.cursor.execute(query, params)
            rows = self.cursor.fetchall()
            
            return [dict(row) for row in rows]
            
        except Exception as e:
            print(f"❌ Ошибка получения документов: {e}")
            return []
    def _document_date_expr(self) -> str:
        return (
            "CASE "
            "WHEN d.reg_date LIKE '__.__.____' "
            "THEN date(substr(d.reg_date, 7, 4) || '-' || substr(d.reg_date, 4, 2) || '-' || substr(d.reg_date, 1, 2)) "
            "ELSE date(d.reg_date) "
            "END"
        )

    def _append_document_filter_clauses(self, where_clauses, params, filters: Dict = None):
        if not filters:
            return

        date_expr = self._document_date_expr()

        search_text = str(filters.get('search_text') or '').strip()
        if search_text:
            where_clauses.append(
                "(d.title LIKE ? OR d.reg_number LIKE ? OR d.number LIKE ? OR d.document_path LIKE ?)"
            )
            search_pattern = f"%{search_text}%"
            params.extend([search_pattern, search_pattern, search_pattern, search_pattern])

        text_filters = {
            'reg_number': 'd.reg_number',
            'number': 'd.number',
            'title': 'd.title',
            'status': 's.name',
            'type': 'dt.name',
            'document_kind': 'dk.name',
            'executor': 'e.name',
            'theme': 't.name',
        }
        for filter_name, column_name in text_filters.items():
            value = str(filters.get(filter_name) or '').strip()
            if value:
                where_clauses.append(f"{column_name} LIKE ?")
                params.append(f"%{value}%")

        if filters.get('status_id'):
            where_clauses.append("d.status_id = ?")
            params.append(filters['status_id'])
        if filters.get('type_id'):
            where_clauses.append("d.type_id = ?")
            params.append(filters['type_id'])
        if filters.get('document_kind_id'):
            where_clauses.append("d.document_kind_id = ?")
            params.append(filters['document_kind_id'])
        if filters.get('executor_id'):
            where_clauses.append("d.executor_id = ?")
            params.append(filters['executor_id'])
        if filters.get('theme_id'):
            where_clauses.append("d.theme_id = ?")
            params.append(filters['theme_id'])

        if filters.get('date_exact'):
            where_clauses.append(f"{date_expr} = date(?)")
            params.append(filters['date_exact'])
        else:
            if filters.get('date_from'):
                where_clauses.append(f"{date_expr} >= date(?)")
                params.append(filters['date_from'])
            if filters.get('date_to'):
                where_clauses.append(f"{date_expr} <= date(?)")
                params.append(filters['date_to'])

        if filters.get('year'):
            where_clauses.append(f"strftime('%Y', {date_expr}) = ?")
            params.append(str(filters['year']))
            if filters.get('month'):
                where_clauses.append(f"CAST(strftime('%m', {date_expr}) AS INTEGER) = ?")
                params.append(int(filters['month']))

    def get_documents_paginated(self, page: int = 1, per_page: int = 100, filters: Dict = None) -> tuple:
        """
        Получить документы с пагинацией для производительности
        
        Args:
            page: Номер страницы (начиная с 1)
            per_page: Количество документов на странице
            filters: Словарь с фильтрами поиска
        
        Returns:
            tuple: (documents: List[Dict], total_count: int, has_more: bool)
        """
        try:
            offset = (page - 1) * per_page
            
            # Строим WHERE условия
            where_clauses = []
            params = []
            
            self._append_document_filter_clauses(where_clauses, params, filters)
            
            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            
            # ШАГ 1: Получаем общее количество (БЫСТРО благодаря индексам)
            count_query = f"""
                SELECT COUNT(*) 
                FROM documents d
                LEFT JOIN ref_status s ON d.status_id = s.id
                LEFT JOIN ref_document_types dt ON d.type_id = dt.id
                LEFT JOIN ref_document_kinds dk ON d.document_kind_id = dk.id
                LEFT JOIN ref_executors e ON d.executor_id = e.id
                LEFT JOIN ref_themes t ON d.theme_id = t.id
                {where_sql}
            """
            
            self.cursor.execute(count_query, params)
            total_count = self.cursor.fetchone()[0]
            
            # ШАГ 2: Получаем данные для текущей страницы
            data_query = f"""
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
                    CASE
                        WHEN d.reg_date IS NULL OR TRIM(d.reg_date) = '' THEN 0
                        ELSE 1
                    END ASC,
                    d.reg_date DESC,
                    d.id DESC
                LIMIT ? OFFSET ?
            """
            
            data_params = params + [per_page, offset]
            self.cursor.execute(data_query, data_params)
            rows = self.cursor.fetchall()
            
            # Преобразуем в список словарей
            documents = [dict(row) for row in rows]
            
            # Проверяем есть ли еще документы
            has_more = (offset + per_page) < total_count
            
            print(f"📊 Пагинация: страница {page}, загружено {len(documents)}, всего {total_count}, есть еще: {has_more}")
            
            return documents, total_count, has_more
            
        except Exception as e:
            print(f"❌ Ошибка пагинации: {e}")
            import traceback
            traceback.print_exc()
            return [], 0, False
    
    def search_documents(self, keyword: str, limit: int = 100) -> List[Dict]:
        """Поиск документов по ключевому слову"""
        try:
            search_pattern = f"%{keyword}%"
            
            query = """
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
            """
            
            params = [search_pattern] * 5 + [limit]
            
            self.cursor.execute(query, params)
            rows = self.cursor.fetchall()
            
            return [dict(row) for row in rows]
            
        except Exception as e:
            print(f"❌ Ошибка поиска документов: {e}")
            return []
    @retry_on_busy(max_attempts=5, delay=0.5)
    def delete_document(self, doc_id: int):
        """Удалить документ"""
        try:
            with self.transaction(immediate=True):
            # Удаляем связанные записи
                self.cursor.execute("DELETE FROM document_signers WHERE document_id = ?", (doc_id,))
                self.cursor.execute("DELETE FROM document_approvers WHERE document_id = ?", (doc_id,))
                
                # Удаляем сам документ
                self.cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            
            
            print(f"✅ Документ ID {doc_id} удален")
        except DatabaseLockError:
            print(f"❌ Не удалось добавить документ: БД заблокирована")
            raise    
        except Exception as e:
            print(f"❌ Ошибка удаления документа: {e}")
            self.conn.rollback()
            raise
    
    # ==================== СПРАВОЧНИКИ ====================
    
    def get_ref_items(self, table_name: str) -> List[Dict]:
        """Получить элементы справочника"""
        try:
            query = f"SELECT id, name FROM {table_name} ORDER BY name"
            self.cursor.execute(query)
            return [dict(row) for row in self.cursor.fetchall()]
        except Exception as e:
            print(f"❌ Ошибка получения справочника {table_name}: {e}")
            return []
    @retry_on_busy(max_attempts=5, delay=0.5)
    def add_ref_item(self, table_name: str, name: str) -> Optional[int]:
        """Добавить элемент в справочник"""
        try:
            with self.transaction(immediate=True):
                query = f"INSERT INTO {table_name} (name) VALUES (?)"
                self.cursor.execute(query, (name,))

            return self.cursor.lastrowid
        except DatabaseLockError:
            print(f"❌ Не удалось добавить документ: БД заблокирована")
            raise
        except Exception as e:
            print(f"❌ Ошибка добавления в справочник: {e}")
            return None
    
    # ==================== ПОДПИСАНТЫ И СОГЛАСУЮЩИЕ ====================
    @retry_on_busy(max_attempts=5, delay=0.5)
    def _add_document_signers(self, document_id: int, signer_ids: List[int]):
        """Добавить подписантов документа"""
        for signer_id in self._normalize_id_list(signer_ids):
            self.cursor.execute(
                "INSERT OR IGNORE INTO document_signers (document_id, signer_id) VALUES (?, ?)",
                (document_id, signer_id)
            )
    @retry_on_busy(max_attempts=5, delay=0.5)
    def _update_document_signers(self, document_id: int, signer_ids: List[int]):
        """Обновить подписантов документа"""
        # Удаляем старые связи
        self.cursor.execute("DELETE FROM document_signers WHERE document_id = ?", (document_id,))
        # Добавляем новые
        self._add_document_signers(document_id, signer_ids)
    
    def _get_document_signers(self, document_id: int) -> List[Dict]:
        """Получить подписантов документа"""
        try:
            query = """
                SELECT rs.id, rs.name
                FROM document_signers ds
                JOIN ref_signers rs ON ds.signer_id = rs.id
                WHERE ds.document_id = ?
                ORDER BY rs.name
            """
            with self._db_lock:
                cursor = self.conn.cursor()
                cursor.execute(query, (document_id,))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            print(f"❌ Ошибка получения подписантов: {e}")
            return []
    @retry_on_busy(max_attempts=5, delay=0.5)
    def _add_document_approvers(self, document_id: int, approver_ids: List[int]):
        """Добавить согласующих документа"""
        for approver_id in self._normalize_id_list(approver_ids):
            self.cursor.execute(
                "INSERT OR IGNORE INTO document_approvers (document_id, approver_id) VALUES (?, ?)",
                (document_id, approver_id)
            )
    @retry_on_busy(max_attempts=5, delay=0.5)
    def _update_document_approvers(self, document_id: int, approver_ids: List[int]):
        """Обновить согласующих документа"""
        # Удаляем старые связи
        self.cursor.execute("DELETE FROM document_approvers WHERE document_id = ?", (document_id,))
        # Добавляем новые
        self._add_document_approvers(document_id, approver_ids)
    
    def _get_document_approvers(self, document_id: int) -> List[Dict]:
        """Получить согласующих документа"""
        try:
            query = """
                SELECT ra.id, ra.name
                FROM document_approvers da
                JOIN ref_approvers ra ON da.approver_id = ra.id
                WHERE da.document_id = ?
                ORDER BY ra.name
            """
            with self._db_lock:
                cursor = self.conn.cursor()
                cursor.execute(query, (document_id,))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            print(f"❌ Ошибка получения согласующих: {e}")
            return []
    
    # ==================== СТАТИСТИКА ====================
    
    def get_documents_count(self) -> int:
        """Получить количество документов"""
        try:
            self.cursor.execute("SELECT COUNT(*) FROM documents")
            return self.cursor.fetchone()[0]
        except Exception as e:
            print(f"❌ Ошибка подсчета документов: {e}")
            return 0
    
    def get_statistics(self) -> Dict:
        """Получить статистику по БД"""
        try:
            stats = {}
            
            # Общее количество
            stats['total_documents'] = self.get_documents_count()
            
            # По статусам
            self.cursor.execute("""
                SELECT s.name, COUNT(d.id) as count
                FROM ref_status s
                LEFT JOIN documents d ON s.id = d.status_id
                GROUP BY s.id, s.name
                ORDER BY count DESC
            """)
            stats['by_status'] = [dict(row) for row in self.cursor.fetchall()]
            
            # По типам
            self.cursor.execute("""
                SELECT dt.name, COUNT(d.id) as count
                FROM ref_document_types dt
                LEFT JOIN documents d ON dt.id = d.type_id
                GROUP BY dt.id, dt.name
                ORDER BY count DESC
            """)
            stats['by_type'] = [dict(row) for row in self.cursor.fetchall()]
            
            return stats
            
        except Exception as e:
            print(f"❌ Ошибка получения статистики: {e}")
            return {}
    
    # ==================== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ====================
    
    def execute_query(self, query: str, params: tuple = None) -> List[Dict]:
        """Выполнить SELECT запрос"""
        try:
            with self._db_lock:
                cursor = self.conn.cursor()
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            print(f"❌ Ошибка выполнения запроса: {e}")
            return []
    @retry_on_busy(max_attempts=5, delay=0.5)
    def execute_update(self, query: str, params: tuple = None) -> Optional[int]:
        """Выполнить INSERT/UPDATE/DELETE запрос"""
        try:
            with self.transaction(immediate=True) as cursor:
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                
            return cursor.lastrowid
        except DatabaseLockError:
            print(f"❌ Не удалось добавить документ: БД заблокирована")
            raise
        except Exception as e:
            print(f"❌ Ошибка выполнения обновления: {e}")
            raise
    def get_documents_statistics(self):
        """Получить статистику по документам"""
        try:
            stats = {}
            
            # Общее количество
            stats['total_documents'] = self.get_documents_count()
            
            # По статусам
            self.cursor.execute("""
                SELECT s.name, COUNT(d.id) as count
                FROM ref_status s
                LEFT JOIN documents d ON s.id = d.status_id
                GROUP BY s.id, s.name
                ORDER BY count DESC
            """)
            stats['by_status'] = {row[0]: row[1] for row in self.cursor.fetchall()}
            
            # По типам
            self.cursor.execute("""
                SELECT dt.name, COUNT(d.id) as count
                FROM ref_document_types dt
                LEFT JOIN documents d ON dt.id = d.type_id
                GROUP BY dt.id, dt.name
                ORDER BY count DESC
            """)
            stats['by_type'] = {row[0]: row[1] for row in self.cursor.fetchall()}
            
            # По исполнителям (топ 10)
            self.cursor.execute("""
                SELECT e.name, COUNT(d.id) as count
                FROM ref_executors e
                LEFT JOIN documents d ON e.id = d.executor_id
                GROUP BY e.id, e.name
                HAVING count > 0
                ORDER BY count DESC
                LIMIT 10
            """)
            stats['by_executor'] = {row[0]: row[1] for row in self.cursor.fetchall()}
            
            return stats
            
        except Exception as e:
            print(f"❌ Ошибка получения статистики: {e}")
            return {
                'total_documents': 0,
                'by_status': {},
                'by_type': {},
                'by_executor': {}
            }

    def get_compact_documents(
        self,
        filters: Optional[Dict[str, Any]] = None,
        document_ids: Optional[Iterable[Any]] = None,
        limit: Optional[int] = 100,
    ) -> List[Dict]:
        try:
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

            id_list = self._normalize_id_list(list(document_ids) if document_ids is not None else None)
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

            return self.execute_query(query, tuple(params))
        except Exception as e:
            print(f"❌ Ошибка получения компактного списка документов: {e}")
            return []

    def get_documents_for_period(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> List[Dict]:
        try:
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
            rows = self.execute_query(query, tuple(params))

            documents: List[Dict] = []
            for row in rows:
                document = self.get_document_by_id(int(row["id"]))
                if document:
                    documents.append(document)
            return documents
        except Exception as e:
            print(f"❌ Ошибка получения документов за период: {e}")
            return []
    def get_simple_reference(self, table_name: str) -> List[Dict]:
        """
        Получить список элементов простого справочника
        
        Args:
            table_name: Название таблицы (ref_status, ref_document_types и т.д.)
        
        Returns:
            List[Dict]: Список элементов справочника [{'id': ..., 'name': ...}, ...]
        """
        try:
            query = f"SELECT id, name FROM {table_name} ORDER BY name"
            return self.execute_query(query)
        except Exception as e:
            print(f"❌ Ошибка получения справочника {table_name}: {e}")
            return []

    def get_reference_id_by_name(self, table_name: str, name: str) -> Optional[int]:
        try:
            query = f"SELECT id FROM {table_name} WHERE name = ?"
            rows = self.execute_query(query, (name,))
            return int(rows[0]["id"]) if rows else None
        except Exception as e:
            print(f"❌ Ошибка получения ID из {table_name}: {e}")
            return None

    def add_simple_reference(self, table_name: str, name: str) -> Optional[int]:
        """
        Добавить элемент в простой справочник
        
        Args:
            table_name: Название таблицы
            name: Название элемента
        
        Returns:
            Optional[int]: ID добавленного элемента или None при ошибке
        """
        try:
            query = f"INSERT INTO {table_name} (name) VALUES (?)"
            return self.execute_update(query, (name,))
        except Exception as e:
            print(f"❌ Ошибка добавления в справочник {table_name}: {e}")
            return None

    def update_simple_reference(self, table_name: str, item_id: int, name: str):
        """
        Обновить элемент простого справочника
        
        Args:
            table_name: Название таблицы
            item_id: ID элемента
            name: Новое название
        """
        try:
            query = f"UPDATE {table_name} SET name = ? WHERE id = ?"
            self.execute_update(query, (name, item_id))
        except Exception as e:
            print(f"❌ Ошибка обновления в справочнике {table_name}: {e}")
            raise
    @retry_on_busy(max_attempts=5, delay=0.5)        
    def delete_simple_reference(self, table_name: str, item_id: int, 
                               foreign_key_field: str = None, check_usage: bool = True):
        """
        Удалить элемент из простого справочника
        
        Args:
            table_name: Название таблицы справочника
            item_id: ID элемента для удаления
            foreign_key_field: Поле в таблице documents для проверки использования
            check_usage: Проверять ли использование элемента перед удалением
        
        Raises:
            Exception: Если элемент используется в документах
        """
        try:
            if check_usage and foreign_key_field:
                # Проверяем использование в документах
                query = f"SELECT COUNT(*) FROM documents WHERE {foreign_key_field} = ?"
                self.cursor.execute(query, (item_id,))
                count = self.cursor.fetchone()[0]
                
                if count > 0:
                    raise Exception(f"Элемент используется в {count} документах")
            
            # Удаляем элемент
            query = f"DELETE FROM {table_name} WHERE id = ?"
            self.execute_update(query, (item_id,))
            
        except Exception as e:
            print(f"❌ Ошибка удаления из справочника {table_name}: {e}")
            raise

    @property
    def connection(self):
        """Получить соединение с БД"""
        return self.conn


# ==================== ПЕРЕКЛЮЧЕНИЕ БАЗ ДАННЫХ ====================

class DatabaseSwitcher:
    """Менеджер для переключения между базами данных"""
    
    def __init__(self, config_file: str = "db_config.json"):
        self.app_root = app_config.get_app_root()
        config_name = config_file or "db_config.json"
        if os.path.isabs(config_name):
            self.config_file = config_name
        else:
            self.config_file = os.path.join(self.app_root, config_name)
        config_data = self.load_config()
        
        # ИЗМЕНЕНИЕ: Распаковываем конфиг правильно
        self.databases = config_data.get('databases', {})
        last_used_db = config_data.get('last_used')
        
        self.current_db = None
        self.current_manager = None
        if last_used_db and last_used_db in self.databases:
            try:
                db_path = self.databases[last_used_db]['path']
                if os.path.exists(db_path):
                    print(f"🔄 Загрузка последней БД: {last_used_db}")
                    self.current_manager = DatabaseManager(db_path)
                    self.current_db = last_used_db
                    print(f"✅ Автоматически подключено к БД: {last_used_db}")
                else:
                    print(f"⚠️ Файл последней БД не найден: {db_path}")
            except Exception as e:
                print(f"⚠️ Не удалось подключиться к последней БД: {e}")
    def _path_to_relative(self, absolute_path: str) -> str:
        """
        Преобразовать абсолютный путь в относительный
        
        Args:
            absolute_path: Полный путь к файлу
        
        Returns:
            str: Относительный путь (например: "data/main2/database.db")
        """
        try:
            # Получаем абсолютные пути для корректного сравнения
            abs_path = os.path.abspath(absolute_path)
            abs_root = os.path.abspath(self.app_root)
            abs_path_norm = os.path.normcase(abs_path)
            abs_root_norm = os.path.normcase(abs_root)
            
            # Проверяем, находится ли путь внутри корневой директории
            if abs_path_norm == abs_root_norm or abs_path_norm.startswith(abs_root_norm + os.sep):
                # Вычисляем относительный путь
                rel_path = os.path.relpath(abs_path, abs_root)
                return rel_path
            else:
                # Если путь вне корневой директории - возвращаем абсолютный
                print(f"⚠️ Путь вне корневой директории: {abs_path}")
                return absolute_path
        except Exception as e:
            print(f"❌ Ошибка преобразования пути в относительный: {e}")
            return absolute_path

    def _path_to_absolute(self, relative_path: str) -> str:
        """
        Преобразовать относительный путь в абсолютный
        
        Args:
            relative_path: Относительный путь
        
        Returns:
            str: Полный абсолютный путь
        """
        try:
            # Если путь уже абсолютный - возвращаем как есть
            if os.path.isabs(relative_path):
                return relative_path
            
            # Объединяем с корневой директорией
            abs_path = os.path.join(self.app_root, relative_path)
            return os.path.abspath(abs_path)
        except Exception as e:
            print(f"❌ Ошибка преобразования пути в абсолютный: {e}")
            return relative_path

    def _resolve_database_path(self, db_name: str, raw_path: str) -> str:
        """
        Нормализовать путь из конфига и мягко мигрировать legacy-форматы.
        """
        normalized = self._path_to_absolute(raw_path)
        if os.path.exists(normalized):
            return normalized

        path_str = str(raw_path)
        path_lower = path_str.replace("/", "\\").lower()
        legacy_marker = "\\src\\data\\"
        marker_index = path_lower.find(legacy_marker)
        if marker_index != -1:
            suffix = path_str[marker_index + len(legacy_marker):].lstrip("\\/")
            legacy_candidates = [
                os.path.abspath(os.path.join(self.app_root, "data", suffix)),
                os.path.abspath(os.path.join(self.app_root, "src", "data", suffix)),
            ]
            for legacy_candidate in legacy_candidates:
                if os.path.exists(legacy_candidate):
                    print(f"[path-migration] legacy db '{db_name}': {raw_path} -> {legacy_candidate}")
                    return legacy_candidate

        fallback_candidates = [
            os.path.abspath(os.path.join(self.app_root, "data", db_name, "database.db")),
            os.path.abspath(os.path.join(self.app_root, "src", "data", db_name, "database.db")),
        ]
        for fallback in fallback_candidates:
            if os.path.exists(fallback):
                print(f"[path-migration] fallback db '{db_name}': {fallback}")
                return fallback

        return normalized
    def load_config(self) -> Dict:
        """Загрузить конфигурацию БД"""
        import json
        
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                
                # Поддержка старого и нового формата
                if isinstance(config_data, dict):
                    if 'databases' in config_data:
                        databases = config_data['databases']
                        
                        # Преобразуем пути и выполняем мягкую миграцию legacy-форматов
                        for db_name, db_info in databases.items():
                            if 'path' in db_info:
                                db_info['path'] = self._resolve_database_path(db_name, db_info['path'])
                        
                        return config_data
                    else:
                        # Старый формат
                        databases = config_data
                        
                        # Преобразуем пути и выполняем мягкую миграцию legacy-форматов
                        for db_name, db_info in databases.items():
                            if 'path' in db_info:
                                db_info['path'] = self._resolve_database_path(db_name, db_info['path'])
                        
                        return {'databases': databases, 'last_used': None}
                else:
                    return {'databases': {}, 'last_used': None}
            except Exception as e:
                print(f"⚠️ Ошибка загрузки конфигурации: {e}")
                return {'databases': {}, 'last_used': None}
        
        return {'databases': {}, 'last_used': None}
    
    def save_config(self):
        """Сохранить конфигурацию БД"""
        import json
        
        try:
            # Создаем копию databases с относительными путями
            databases_to_save = {}
            
            for db_name, db_info in self.databases.items():
                db_info_copy = db_info.copy()
                
                # ✅ НОВОЕ: Преобразуем абсолютные пути в относительные
                if 'path' in db_info_copy:
                    db_info_copy['path'] = self._path_to_relative(db_info_copy['path'])
                
                databases_to_save[db_name] = db_info_copy
            
            config_data = {
                'databases': databases_to_save,
                'last_used': self.current_db
            }
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)
                
            print(f"💾 Конфигурация сохранена с относительными путями (текущая БД: {self.current_db})")
        except Exception as e:
            print(f"❌ Ошибка сохранения конфигурации: {e}")
    
    def add_database(self, name: str, path: str):
        """Добавить БД в список"""
        self.databases[name] = {
            'path': path,
            'added': datetime.now().isoformat()
        }
        self.save_config()  # ← Сохраняем конфиг
        print(f"📝 БД '{name}' добавлена в список")
    
    def switch_database(self, name: str) -> DatabaseManager:
        """Переключиться на другую БД"""
        try:
            if name not in self.databases:
                raise ValueError(f"База данных '{name}' не найдена")
            
            db_path = self.databases[name]['path']
            
            if not os.path.exists(db_path):
                raise FileNotFoundError(f"Файл БД не найден: {db_path}")
            
            # Сначала создаем новое соединение
            print(f"🔌 Подключение к БД: {name}...")
            new_manager = DatabaseManager(db_path)
            
            # Только после успешного создания закрываем старое
            if self.current_manager:
                try:
                    self.current_manager.close()
                except Exception as e:
                    print(f"⚠️ Предупреждение при закрытии старого соединения: {e}")
            
            # Присваиваем только после успеха
            self.current_manager = new_manager
            self.current_db = name
            
            # НОВОЕ: Сохраняем как последнюю использованную
            self.save_config()
            
            print(f"✅ Переключено на БД: {name}")
            return self.current_manager
            
        except Exception as e:
            print(f"❌ Ошибка переключения БД: {e}")
            raise
        
    def create_new_database(self, name: str, path: str) -> DatabaseManager:
        """Создать новую пустую БД с той же схемой"""
        try:
            # Создаем новую БД
            manager = DatabaseManager(path, create_if_not_exists=True)
            
            # Создаем структуру (нужно вызвать create_tables_if_not_exist)
            # Это будет сделано в DatabaseManager.__init__
            
            # Добавляем в конфигурацию
            self.add_database(name, path)
            
            print(f"✅ Создана новая БД: {name}")
            return manager
            
        except Exception as e:
            print(f"❌ Ошибка создания новой БД: {e}")
            raise
    def register_database(self, name: str, path: str, manager: DatabaseManager):
        """
        Зарегистрировать уже созданный DatabaseManager в switcher
        
        🆕 ИЗМЕНЕНИЕ: Убеждаемся, что БД находится в правильной структуре
        """
        # Проверяем, что БД в папке data/(название)/
        db_folder = os.path.dirname(os.path.abspath(path))
        expected_location = os.path.join(self.app_root, "data", name)
        
        # Если БД не в правильном месте - предупреждаем
        if db_folder != expected_location:
            print(f"⚠️ БД '{name}' находится не в стандартной структуре")
            print(f"   Текущее местоположение: {db_folder}")
            print(f"   Ожидаемое: {expected_location}")
        
        self.databases[name] = {
            'path': path,
            'added': datetime.now().isoformat()
        }
        self.current_db = name
        self.current_manager = manager
        self.save_config()
        print(f"✅ БД '{name}' зарегистрирована как текущая")      
    def get_database_list(self) -> List[Dict]:
        """Получить список всех БД"""
        return [
            {'name': name, **info}
            for name, info in self.databases.items()
        ]
    def validate_database_schema(self, db_path: str) -> tuple[bool, str]:
        """
        Проверить схему базы данных на соответствие требованиям
        
        Returns:
            tuple[bool, str]: (валидна ли БД, сообщение об ошибке если не валидна)
        """
        try:
            import sqlite3
            
            # Пытаемся подключиться
            test_conn = sqlite3.connect(db_path, timeout=5.0)
            cursor = test_conn.cursor()
            
            # Проверяем наличие всех необходимых таблиц
            required_tables = [
                'documents',
                'ref_status',
                'ref_document_types',
                'ref_signing_types',
                'ref_document_kinds',
                'ref_themes',
                'ref_executors',
                'ref_responsible_executors',
                'ref_published_where',
                'ref_signers',
                'ref_approvers',
                'document_signers',
                'document_approvers'
            ]
            
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            existing_tables = [row[0] for row in cursor.fetchall()]
            
            missing_tables = [t for t in required_tables if t not in existing_tables]
            
            if missing_tables:
                test_conn.close()
                return False, f"Отсутствуют таблицы: {', '.join(missing_tables)}"
            
            # Проверяем ключевые поля в основной таблице documents
            cursor.execute("PRAGMA table_info(documents)")
            columns = [row[1] for row in cursor.fetchall()]
            
            required_columns = [
                'id', 'reg_number', 'reg_date', 'title', 'status_id', 
                'type_id', 'executor_id', 'theme_id', 'document_path'
            ]
            
            missing_columns = [c for c in required_columns if c not in columns]
            
            if missing_columns:
                test_conn.close()
                return False, f"В таблице documents отсутствуют поля: {', '.join(missing_columns)}"
            
            # Проверяем что БД не пустая (есть хотя бы справочники)
            cursor.execute("SELECT COUNT(*) FROM ref_status")
            status_count = cursor.fetchone()[0]
            
            if status_count == 0:
                test_conn.close()
                return False, "База данных пустая (отсутствуют справочники)"
            
            test_conn.close()
            return True, "✅ Схема базы данных корректна"
            
        except sqlite3.Error as e:
            return False, f"Ошибка SQLite: {str(e)}"
        except Exception as e:
            return False, f"Ошибка проверки: {str(e)}"
    def search_by_tags(self, **kwargs) -> List[Dict]:
        """
        Расширенный поиск документов по множеству критериев
        
        Args:
            **kwargs: Критерии поиска
                - keywords: текст для поиска
                - date_from: дата от (YYYY-MM-DD)
                - date_to: дата до (YYYY-MM-DD)
                - status: статус документа
                - type_doc: тип документа
                - executor_ids: список ID исполнителей
                - theme_ids: список ID тем
                - document_kind: вид документа
                - signing_type: тип подписания
                - should_publish: подлежит опубликованию
                - published_where_id: где опубликовано
                - case_number: номер дела
                - volume_number: номер тома
                - removed_from_control: снято с контроля
        
        Returns:
            List[Dict]: Список найденных документов
        """
        try:
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
            
            params = []
            
            # Текстовый поиск
            if 'keywords' in kwargs and kwargs['keywords']:
                query += """ AND (
                    d.title LIKE ? OR
                    d.reg_number LIKE ? OR
                    d.number LIKE ? OR
                    e.name LIKE ? OR
                    t.name LIKE ?
                )"""
                search_pattern = f"%{kwargs['keywords']}%"
                params.extend([search_pattern] * 5)
            
            # Фильтр по датам
            if 'date_from' in kwargs:
                query += " AND date(d.reg_date) >= date(?)"
                params.append(kwargs['date_from'])
            
            if 'date_to' in kwargs:
                query += " AND date(d.reg_date) <= date(?)"
                params.append(kwargs['date_to'])
            
            # Фильтр по статусу
            if 'status' in kwargs:
                query += " AND s.name = ?"
                params.append(kwargs['status'])
            
            # Фильтр по типу
            if 'type_doc' in kwargs:
                query += " AND dt.name = ?"
                params.append(kwargs['type_doc'])
            
            # Фильтр по виду документа
            if 'document_kind' in kwargs:
                query += " AND dk.name = ?"
                params.append(kwargs['document_kind'])
            
            # Фильтр по типу подписания
            if 'signing_type' in kwargs:
                query += " AND st.name = ?"
                params.append(kwargs['signing_type'])
            
            # Фильтр по исполнителям
            if 'executor_ids' in kwargs and kwargs['executor_ids']:
                placeholders = ','.join(['?' for _ in kwargs['executor_ids']])
                query += f" AND d.executor_id IN ({placeholders})"
                params.extend(kwargs['executor_ids'])
            
            # Фильтр по темам
            if 'theme_ids' in kwargs and kwargs['theme_ids']:
                placeholders = ','.join(['?' for _ in kwargs['theme_ids']])
                query += f" AND d.theme_id IN ({placeholders})"
                params.extend(kwargs['theme_ids'])
            
            # Фильтр по публикации
            if 'should_publish' in kwargs:
                query += " AND d.should_publish = ?"
                params.append(kwargs['should_publish'])
            
            if 'published_where_id' in kwargs:
                query += " AND d.published_where_id = ?"
                params.append(kwargs['published_where_id'])
            
            # Фильтр по делу
            if 'case_number' in kwargs and kwargs['case_number']:
                query += " AND d.case_number LIKE ?"
                params.append(f"%{kwargs['case_number']}%")
            
            if 'volume_number' in kwargs and kwargs['volume_number']:
                query += " AND d.volume_number LIKE ?"
                params.append(f"%{kwargs['volume_number']}%")
            
            # Фильтр по контролю
            if 'removed_from_control' in kwargs:
                query += " AND d.removed_from_control = ?"
                params.append(kwargs['removed_from_control'])
            
            query += " ORDER BY d.reg_date DESC LIMIT 1000"
            
            return self.execute_query(query, tuple(params))
            
        except Exception as e:
            print(f"❌ Ошибка расширенного поиска: {e}")
            return []


    def get_document_signers(self, document_id: int) -> List[Dict]:
        """
        Получить подписантов документа
        
        Args:
            document_id: ID документа
        
        Returns:
            List[Dict]: Список подписантов
        """
        try:
            query = """
                SELECT rs.id, rs.name
                FROM document_signers ds
                JOIN ref_signers rs ON ds.signer_id = rs.id
                WHERE ds.document_id = ?
                ORDER BY rs.name
            """
            return self.execute_query(query, (document_id,))
        except Exception as e:
            print(f"❌ Ошибка получения подписантов: {e}")
            return []


    def get_document_approvers(self, document_id: int) -> List[Dict]:
        """
        Получить согласующих документа
        
        Args:
            document_id: ID документа
        
        Returns:
            List[Dict]: Список согласующих
        """
        try:
            query = """
                SELECT ra.id, ra.name
                FROM document_approvers da
                JOIN ref_approvers ra ON da.approver_id = ra.id
                WHERE da.document_id = ?
                ORDER BY ra.name
            """
            return self.execute_query(query, (document_id,))
        except Exception as e:
            print(f"❌ Ошибка получения согласующих: {e}")
            return []


    def update_document_signers(self, document_id: int, signer_ids: List[int]):
        """
        Обновить подписантов документа
        
        Args:
            document_id: ID документа
            signer_ids: Список ID подписантов
        """
        try:
            # Удаляем старые связи
            self.execute_update("DELETE FROM document_signers WHERE document_id = ?", (document_id,))
            
            # Добавляем новые связи
            for signer_id in signer_ids:
                self.execute_update(
                    "INSERT INTO document_signers (document_id, signer_id) VALUES (?, ?)",
                    (document_id, signer_id)
                )
            
            print(f"✅ Обновлены подписанты для документа {document_id}")
        except Exception as e:
            print(f"❌ Ошибка обновления подписантов: {e}")
            raise


    def update_document_approvers(self, document_id: int, approver_ids: List[int]):
        """
        Обновить согласующих документа
        
        Args:
            document_id: ID документа
            approver_ids: Список ID согласующих
        """
        try:
            # Удаляем старые связи
            self.execute_update("DELETE FROM document_approvers WHERE document_id = ?", (document_id,))
            
            # Добавляем новые связи
            for approver_id in approver_ids:
                self.execute_update(
                    "INSERT INTO document_approvers (document_id, approver_id) VALUES (?, ?)",
                    (document_id, approver_id)
                )
            
            print(f"✅ Обновлены согласующие для документа {document_id}")
        except Exception as e:
            print(f"❌ Ошибка обновления согласующих: {e}")
            raise
