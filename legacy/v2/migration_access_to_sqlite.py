#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🔄 ПРОФЕССИОНАЛЬНЫЙ СКРИПТ МИГРАЦИИ Access → SQLite v4.0

✨ НОВОЕ:
- Поддержка XLSX файлов напрямую (не нужна конвертация)
- Правильная обработка Foreign Keys
- Детальная диагностика проблем с FK
- Режим "мягкого импорта" (пропуск проблемных строк)
"""

import sqlite3
import os
import shutil
from datetime import datetime
from typing import Dict, List, Any, Optional
from pathlib import Path
import pandas as pd


class AccessToSQLiteMigration:
    """Класс для миграции Access -> SQLite с поддержкой XLSX"""
    
    def __init__(self, 
                 data_folder: str, 
                 target_db: str, 
                 test_mode: bool = False, 
                 test_rows: int = 100,
                 skip_invalid_fk: bool = True):
        """
        Args:
            data_folder: Папка с XLSX или CSV файлами
            target_db: Путь к файлу SQLite БД
            test_mode: Тестовый режим (ограничение строк)
            test_rows: Количество строк в тестовом режиме
            skip_invalid_fk: Пропускать строки с битыми FK (рекомендуется True)
        """
        self.data_folder = Path(data_folder)
        self.target_db = Path(target_db)
        self.test_mode = test_mode
        self.test_rows = test_rows
        self.skip_invalid_fk = skip_invalid_fk
        
        # Валидация путей
        self._validate_paths()
        
        # Статистика
        self.stats = {
            'tables_migrated': 0,
            'total_rows': 0,
            'skipped_rows': 0,
            'errors': []
        }
        
        # Кэш для проверки FK
        self.fk_cache = {}
        
        # Логирование
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.log_file = self.target_db.parent / f"migration_{timestamp}.log"
        
    def _validate_paths(self):
        """Валидация путей"""
        if not self.data_folder.exists():
            raise FileNotFoundError(f"❌ Папка не найдена: {self.data_folder}")
        
        if self.target_db.is_dir():
            raise ValueError(
                f"❌ target_db должен быть путем к ФАЙЛУ, а не директорией!\n"
                f"   Текущий: {self.target_db}\n"
                f"   Правильно: {self.target_db / 'documents.db'}"
            )
        
        self.target_db.parent.mkdir(parents=True, exist_ok=True)
        
        print(f"✅ Пути валидны:")
        print(f"   Данные: {self.data_folder}")
        print(f"   БД:     {self.target_db}")
    
    def log(self, message: str):
        """Логирование"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_message = f"[{timestamp}] {message}"
        print(log_message)
        
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(log_message + '\n')
    
    def find_data_file(self, base_name: str) -> Optional[Path]:
        """
        Поиск файла данных (XLSX или CSV)
        
        Args:
            base_name: Базовое имя файла (например, "Т_Статус")
        
        Returns:
            Path к файлу или None
        """
        # Пробуем найти XLSX
        xlsx_file = self.data_folder / f"{base_name}.xlsx"
        if xlsx_file.exists():
            return xlsx_file
        
        # Пробуем найти CSV
        csv_file = self.data_folder / f"{base_name}.csv"
        if csv_file.exists():
            return csv_file
        
        return None
    
    def read_data_file(self, filepath: Path, limit: Optional[int] = None) -> pd.DataFrame:
        """
        Чтение XLSX или CSV файла
        
        Args:
            filepath: Путь к файлу
            limit: Ограничение строк (для тестового режима)
        
        Returns:
            DataFrame с данными
        """
        try:
            if filepath.suffix == '.xlsx':
                df = pd.read_excel(filepath, nrows=limit)
            elif filepath.suffix == '.csv':
                # Пробуем разные кодировки
                for encoding in ['utf-8-sig', 'utf-8', 'cp1251']:
                    try:
                        df = pd.read_csv(filepath, encoding=encoding, nrows=limit)
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    raise ValueError(f"Не удалось определить кодировку: {filepath}")
            else:
                raise ValueError(f"Неподдерживаемый формат: {filepath.suffix}")
            
            return df
            
        except Exception as e:
            self.log(f"❌ Ошибка чтения файла {filepath}: {e}")
            raise
    
    def backup_database(self):
        """Резервное копирование"""
        if self.target_db.exists():
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = self.target_db.parent / f"{self.target_db.stem}_backup_{timestamp}{self.target_db.suffix}"
            shutil.copy2(self.target_db, backup_path)
            self.log(f"💾 Бэкап: {backup_path.name}")
            return backup_path
        return None
    
    def create_database_schema(self, conn: sqlite3.Connection):
        """Создание структуры БД"""
        self.log("🏗️ Создание структуры БД...")
        
        cursor = conn.cursor()
        
        # ✅ ВАЖНО: Отключаем FK во время создания схемы и импорта!
        cursor.execute("PRAGMA foreign_keys = OFF")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")
        
        # Справочники
        tables = {
            'ref_status': 'id INTEGER PRIMARY KEY, name TEXT',
            'ref_document_types': 'id INTEGER PRIMARY KEY, name TEXT',
            'ref_signing_types': 'id INTEGER PRIMARY KEY, name TEXT',
            'ref_document_kinds': 'id INTEGER PRIMARY KEY, name TEXT',
            'ref_published_where': 'id INTEGER PRIMARY KEY, name TEXT',
            'ref_executors': 'id INTEGER PRIMARY KEY, name TEXT',
            'ref_responsible_executors': 'id INTEGER PRIMARY KEY, name TEXT',
            'ref_themes': 'id INTEGER PRIMARY KEY, name TEXT',
            'ref_signers': 'id INTEGER PRIMARY KEY, name TEXT',
            'ref_approvers': 'id INTEGER PRIMARY KEY, name TEXT'
        }
        
        for table_name, columns in tables.items():
            cursor.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({columns})")
        
        # Главная таблица документов (без FK constraints)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY,
                status_id INTEGER,
                type_id INTEGER,
                signing_type_id INTEGER,
                reg_number TEXT,
                reg_date TEXT,
                executor_id INTEGER,
                theme_id INTEGER,
                title TEXT,
                document_kind_id INTEGER,
                should_publish TEXT,
                number TEXT,
                published_where_id INTEGER,
                published_date TEXT,
                responsible_executor_id INTEGER,
                control_date TEXT,
                execution_result TEXT,
                removed_from_control TEXT,
                pages_count INTEGER,
                attachments_count INTEGER,
                case_number TEXT,
                volume_number TEXT,
                sheets TEXT,
                document_path TEXT
            )
        """)
        
        # Связующие таблицы
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS document_signers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                signer_id INTEGER NOT NULL,
                UNIQUE(document_id, signer_id)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS document_approvers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                approver_id INTEGER,
                UNIQUE(document_id, approver_id)
            )
        """)
        
        conn.commit()
        self.log("✅ Структура создана")
    
    def load_fk_cache(self, conn: sqlite3.Connection, table: str, column: str = 'id'):
        """Загрузка существующих ID в кэш для проверки FK"""
        cursor = conn.cursor()
        cursor.execute(f"SELECT {column} FROM {table}")
        self.fk_cache[table] = set(row[0] for row in cursor.fetchall() if row[0] is not None)
        self.log(f"   📋 Загружено {len(self.fk_cache[table])} ID из {table}")
    
    def import_reference_table(self, 
                              conn: sqlite3.Connection,
                              file_base_name: str,
                              table_name: str,
                              id_column: str,
                              name_column: str):
        """Импорт справочной таблицы"""
        filepath = self.find_data_file(file_base_name)
        
        if not filepath:
            self.log(f"⚠️ Файл не найден: {file_base_name}")
            return
        
        self.log(f"\n📥 {file_base_name} → {table_name}")
        
        try:
            # Читаем данные
            limit = self.test_rows if self.test_mode else None
            df = self.read_data_file(filepath, limit)
            
            # Подготовка данных
            if id_column not in df.columns or name_column not in df.columns:
                self.log(f"❌ Отсутствуют колонки: {id_column}, {name_column}")
                self.log(f"   Доступные: {', '.join(df.columns)}")
                return
            
            # Очистка данных
            df = df[[id_column, name_column]].copy()
            df = df.dropna(subset=[id_column])  # Удаляем строки без ID
            df[id_column] = df[id_column].astype(int)
            df[name_column] = df[name_column].fillna('').astype(str)
            
            # Конвертация значений для SQLite
            def convert_value(val):
                """Конвертация значений для SQLite"""
                if pd.isna(val):
                    return None
                if isinstance(val, pd.Timestamp):
                    return val.strftime('%Y-%m-%d')
                if hasattr(val, 'isoformat'):
                    return val.isoformat()
                if hasattr(val, 'item'):
                    return val.item()
                return val
            
            # Batch insert
            cursor = conn.cursor()
            data = [(convert_value(row[id_column]), convert_value(row[name_column])) for _, row in df.iterrows()]
            
            cursor.executemany(
                f"INSERT OR IGNORE INTO {table_name} (id, name) VALUES (?, ?)",
                data
            )
            conn.commit()
            
            rows_imported = len(data)
            self.stats['tables_migrated'] += 1
            self.stats['total_rows'] += rows_imported
            
            self.log(f"   ✅ Импортировано: {rows_imported:,} строк")
            
        except Exception as e:
            self.log(f"   ❌ Ошибка: {e}")
            self.stats['errors'].append(f"{table_name}: {e}")
            raise
    
    def import_documents_table(self, conn: sqlite3.Connection):
        """Импорт главной таблицы документов с валидацией FK"""
        filepath = self.find_data_file("Т_НормативныеАкты")
        
        if not filepath:
            self.log("❌ Файл с документами не найден!")
            return
        
        self.log(f"\n📄 Импорт документов из {filepath.name}")
        
        # Загружаем FK кэши
        self.log("📋 Подготовка проверки внешних ключей...")
        self.load_fk_cache(conn, 'ref_status')
        self.load_fk_cache(conn, 'ref_document_types')
        self.load_fk_cache(conn, 'ref_signing_types')
        self.load_fk_cache(conn, 'ref_executors')
        self.load_fk_cache(conn, 'ref_themes')
        self.load_fk_cache(conn, 'ref_document_kinds')
        self.load_fk_cache(conn, 'ref_published_where')
        self.load_fk_cache(conn, 'ref_responsible_executors')
        
        # Читаем документы
        limit = self.test_rows if self.test_mode else None
        df = self.read_data_file(filepath, limit)
        
        self.log(f"📊 Загружено строк: {len(df):,}")
        
        # Маппинг колонок
        column_mapping = {
            "Код": "id",
            "Статус документа": "status_id",
            "Тип документа": "type_id",
            "Подписание документа": "signing_type_id",
            "Регистрационный номер": "reg_number",
            "Регистрационная дата": "reg_date",
            "ИсполнительДокумента": "executor_id",
            "Тема": "theme_id",
            "Заголовок": "title",
            "Вид документа": "document_kind_id",
            "Подлежит ли опубликованию": "should_publish",
            "№": "number",
            "Опубликовано": "published_where_id",
            "Дата публикации": "published_date",
            "Ответственный исполнитель": "responsible_executor_id",
            "Дата контроля": "control_date",
            "Результат исполнения": "execution_result",
            "С контроля снято": "removed_from_control",
            "Количество листов": "pages_count",
            "Количество приложений": "attachments_count",
            "Дело №": "case_number",
            "Том №": "volume_number",
            "Листы": "sheets",
            "Документ": "document_path"
        }
        
        # Переименовываем колонки
        df = df.rename(columns=column_mapping)
        
        # FK валидация и очистка
        fk_checks = {
            'status_id': 'ref_status',
            'type_id': 'ref_document_types',
            'signing_type_id': 'ref_signing_types',
            'executor_id': 'ref_executors',
            'theme_id': 'ref_themes',
            'document_kind_id': 'ref_document_kinds',
            'published_where_id': 'ref_published_where',
            'responsible_executor_id': 'ref_responsible_executors'
        }
        
        if self.skip_invalid_fk:
            self.log("🔍 Валидация и очистка внешних ключей...")
            
            initial_count = len(df)
            
            for fk_column, ref_table in fk_checks.items():
                if fk_column in df.columns:
                    # Заменяем битые FK на NULL
                    valid_ids = self.fk_cache.get(ref_table, set())
                    
                    def validate_fk(value):
                        if pd.isna(value):
                            return None
                        try:
                            int_value = int(value)
                            return int_value if int_value in valid_ids else None
                        except:
                            return None
                    
                    before = df[fk_column].notna().sum()
                    df[fk_column] = df[fk_column].apply(validate_fk)
                    after = df[fk_column].notna().sum()
                    
                    if before != after:
                        self.log(f"   ⚠️ {fk_column}: {before - after} битых ссылок заменено на NULL")
        
        # Вставка данных
        cursor = conn.cursor()
        
        # Подготовка SQL
        columns = [col for col in column_mapping.values() if col in df.columns]
        placeholders = ','.join(['?' for _ in columns])
        insert_sql = f"INSERT OR IGNORE INTO documents ({','.join(columns)}) VALUES ({placeholders})"
        
        # Batch insert
        self.log("💾 Импорт данных...")
        batch = []
        batch_size = 1000
        rows_imported = 0
        
        def convert_value(val):
            """Конвертация значений для SQLite"""
            if pd.isna(val):
                return None
            # Конвертируем pandas Timestamp в строку
            if isinstance(val, pd.Timestamp):
                return val.strftime('%Y-%m-%d')
            # Конвертируем другие даты
            if hasattr(val, 'isoformat'):
                return val.isoformat()
            # Обрабатываем numpy типы
            if hasattr(val, 'item'):
                return val.item()
            return val
        
        for idx, row in df.iterrows():
            values = tuple(convert_value(row[col]) if col in df.columns else None for col in columns)
            batch.append(values)
            
            if len(batch) >= batch_size:
                cursor.executemany(insert_sql, batch)
                rows_imported += len(batch)
                batch = []
                
                if rows_imported % 10000 == 0:
                    self.log(f"   📊 Импортировано: {rows_imported:,}")
        
        # Остаток
        if batch:
            cursor.executemany(insert_sql, batch)
            rows_imported += len(batch)
        
        conn.commit()
        
        self.stats['tables_migrated'] += 1
        self.stats['total_rows'] += rows_imported
        
        self.log(f"   ✅ Импортировано документов: {rows_imported:,}")
    
    def import_link_table(self,
                         conn: sqlite3.Connection,
                         file_base_name: str,
                         table_name: str,
                         doc_column: str,
                         ref_column: str):
        """Импорт связующих таблиц"""
        filepath = self.find_data_file(file_base_name)
        
        if not filepath:
            self.log(f"⚠️ Файл не найден: {file_base_name}")
            return
        
        self.log(f"\n🔗 {file_base_name} → {table_name}")
        
        try:
            # Читаем данные
            limit = self.test_rows if self.test_mode else None
            df = self.read_data_file(filepath, limit)
            
            # Проверка колонок
            if doc_column not in df.columns or ref_column not in df.columns:
                self.log(f"❌ Отсутствуют колонки")
                self.log(f"   Ожидаем: {doc_column}, {ref_column}")
                self.log(f"   Есть: {', '.join(df.columns)}")
                return
            
            # Очистка данных
            df = df[[doc_column, ref_column]].copy()
            df = df.dropna()  # Удаляем строки с NULL
            df[doc_column] = df[doc_column].astype(int)
            
            # Пытаемся преобразовать ref_column в int
            try:
                df[ref_column] = df[ref_column].astype(int)
            except:
                pass  # Если не получится, оставляем как есть
            
            # Конвертация значений для SQLite
            def convert_value(val):
                """Конвертация значений для SQLite"""
                if pd.isna(val):
                    return None
                if isinstance(val, pd.Timestamp):
                    return val.strftime('%Y-%m-%d')
                if hasattr(val, 'isoformat'):
                    return val.isoformat()
                if hasattr(val, 'item'):
                    return val.item()
                return val
            
            # Валидация FK
            if self.skip_invalid_fk:
                # Проверяем что document_id существует
                if 'documents' not in self.fk_cache:
                    self.load_fk_cache(conn, 'documents')
                
                valid_docs = self.fk_cache['documents']
                initial_count = len(df)
                df = df[df[doc_column].isin(valid_docs)]
                
                if len(df) < initial_count:
                    self.log(f"   ⚠️ Пропущено {initial_count - len(df)} строк с битыми ссылками")
            
            # Вставка
            cursor = conn.cursor()
            data = [(convert_value(row[doc_column]), convert_value(row[ref_column])) for _, row in df.iterrows()]
            
            # Определяем правильное название колонки
            if table_name == 'document_signers':
                ref_col_name = 'signer_id'
            elif table_name == 'document_approvers':
                ref_col_name = 'approver_id'
            else:
                ref_col_name = table_name.replace('document_', '') + '_id'
            
            cursor.executemany(
                f"INSERT OR IGNORE INTO {table_name} (document_id, {ref_col_name}) VALUES (?, ?)",
                data
            )
            conn.commit()
            
            rows_imported = len(data)
            self.stats['tables_migrated'] += 1
            self.stats['total_rows'] += rows_imported
            
            self.log(f"   ✅ Импортировано: {rows_imported:,} связей")
            
        except Exception as e:
            self.log(f"   ❌ Ошибка: {e}")
            self.stats['errors'].append(f"{table_name}: {e}")
    
    def create_indexes(self, conn: sqlite3.Connection):
        """Создание индексов"""
        self.log("\n📑 Создание индексов...")
        
        cursor = conn.cursor()
        
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_docs_status ON documents(status_id)",
            "CREATE INDEX IF NOT EXISTS idx_docs_type ON documents(type_id)",
            "CREATE INDEX IF NOT EXISTS idx_docs_executor ON documents(executor_id)",
            "CREATE INDEX IF NOT EXISTS idx_docs_theme ON documents(theme_id)",
            "CREATE INDEX IF NOT EXISTS idx_docs_reg_date ON documents(reg_date)",
            "CREATE INDEX IF NOT EXISTS idx_docs_reg_number ON documents(reg_number)",
            "CREATE INDEX IF NOT EXISTS idx_doc_signers_doc ON document_signers(document_id)",
            "CREATE INDEX IF NOT EXISTS idx_doc_signers_signer ON document_signers(signer_id)",
            "CREATE INDEX IF NOT EXISTS idx_doc_approvers_doc ON document_approvers(document_id)",
            "CREATE INDEX IF NOT EXISTS idx_doc_approvers_approver ON document_approvers(approver_id)"
        ]
        
        for idx_sql in indexes:
            cursor.execute(idx_sql)
        
        conn.commit()
        self.log("✅ Индексы созданы")
    
    def migrate(self) -> bool:
        """Основной процесс миграции"""
        try:
            self.log("\n" + "="*80)
            self.log("🚀 НАЧАЛО МИГРАЦИИ")
            self.log("="*80)
            self.log(f"  Режим: {'ТЕСТОВЫЙ' if self.test_mode else 'ПОЛНЫЙ'}")
            self.log(f"  Пропуск битых FK: {'ДА' if self.skip_invalid_fk else 'НЕТ'}")
            if self.test_mode:
                self.log(f"  Лимит строк: {self.test_rows}")
            
            # Резервная копия
            self.backup_database()
            
            # Подключение
            self.log("\n🔌 Подключение к SQLite...")
            conn = sqlite3.connect(str(self.target_db))
            
            # Создание структуры
            self.create_database_schema(conn)
            
            # Импорт справочников
            self.log("\n" + "="*80)
            self.log("📚 ШАГ 1: Импорт справочников")
            self.log("="*80)
            
            refs = [
                ("Т_Статус", "ref_status", "Код", "Статус документа"),
                ("Т_ТипДокумента", "ref_document_types", "Код", "Тип документа"),
                ("Т_Подписание", "ref_signing_types", "Код", "Подписание"),
                ("Т_ВидДокумента", "ref_document_kinds", "Код", "Вид документа"),
                ("Т_Опубликовано", "ref_published_where", "Код", "Опубликовано"),
                ("Т_Исполнитель", "ref_executors", "Код", "Исполнитель"),
                ("Т_ОтветственныйИсполнитель", "ref_responsible_executors", "Код", "ФИО"),
                ("Т_Тема", "ref_themes", "Код", "Тема"),
                ("Т_КтоПодписал", "ref_signers", "Код", "Кто подписал"),
                ("Т_Согласование", "ref_approvers", "Код", "ФИО"),
            ]
            
            for file_name, table, id_col, name_col in refs:
                self.import_reference_table(conn, file_name, table, id_col, name_col)
            
            # Импорт документов
            self.log("\n" + "="*80)
            self.log("📄 ШАГ 2: Импорт документов")
            self.log("="*80)
            
            self.import_documents_table(conn)
            
            # Импорт связей
            self.log("\n" + "="*80)
            self.log("🔗 ШАГ 3: Импорт связей")
            self.log("="*80)
            
            self.import_link_table(
                conn,
                "Т_НормативныеАкты подчин",
                "document_signers",
                "КодДокумента",
                "Кто подписал"
            )
            
            self.import_link_table(
                conn,
                "Т_НормативныеАкты подчин2",
                "document_approvers",
                "КодДокумента",
                "Согласовано"
            )
            
            # Индексы
            self.create_indexes(conn)
            
            # Оптимизация
            self.log("\n🔧 Оптимизация...")
            conn.execute("ANALYZE")
            conn.execute("VACUUM")
            
            # Статистика
            self.log("\n" + "="*80)
            self.log("📊 ИТОГОВАЯ СТАТИСТИКА")
            self.log("="*80)
            self.log(f"  Таблиц мигрировано: {self.stats['tables_migrated']}")
            self.log(f"  Строк импортировано: {self.stats['total_rows']:,}")
            self.log(f"  Пропущено (битые FK): {self.stats['skipped_rows']:,}")
            self.log(f"  Ошибок: {len(self.stats['errors'])}")
            
            if self.stats['errors']:
                self.log("\n❌ Список ошибок:")
                for error in self.stats['errors']:
                    self.log(f"  - {error}")
            
            conn.close()
            
            self.log("\n" + "="*80)
            self.log("✅ МИГРАЦИЯ ЗАВЕРШЕНА!")
            self.log("="*80)
            self.log(f"📁 БД: {self.target_db}")
            self.log(f"📝 Лог: {self.log_file}")
            
            return True
            
        except Exception as e:
            self.log(f"\n❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
            import traceback
            self.log(traceback.format_exc())
            return False


def main():
    """Главная функция"""
    
    print("""
╔══════════════════════════════════════════════════════════════╗
║       МИГРАЦИЯ Access → SQLite v4.0                          ║
║       Поддержка XLSX + Исправление FK                        ║
╚══════════════════════════════════════════════════════════════╝
    """)
    
    # ✅ НАСТРОЙКИ
    DATA_FOLDER = r"D:\python_ex\acti_v2\dist\ACTI_DocumentManager\data\район\access_data"  # Папка с XLSX или CSV
    TARGET_DB = r"D:\python_ex\acti_v2\dist\ACTI_DocumentManager\data\район\database.db"  # Файл БД
    
    TEST_MODE = False  # Тестовый режим
    TEST_ROWS = 1000  # Количество строк для теста
    
    SKIP_INVALID_FK = True  # Пропускать битые внешние ключи (рекомендуется!)
    
    print(f"\n📂 Данные: {DATA_FOLDER}")
    print(f"💾 БД:     {TARGET_DB}")
    print(f"⚙️  Режим:  {'ТЕСТОВЫЙ' if TEST_MODE else 'ПОЛНЫЙ'}")
    print(f"🔧 Битые FK: {'Пропускать' if SKIP_INVALID_FK else 'Ошибка'}")
    
    response = input("\n➡️  Начать миграцию? (yes/no): ").lower()
    
    if response != 'yes':
        print("❌ Миграция отменена")
        return
    
    try:
        migrator = AccessToSQLiteMigration(
            data_folder=DATA_FOLDER,
            target_db=TARGET_DB,
            test_mode=TEST_MODE,
            test_rows=TEST_ROWS,
            skip_invalid_fk=SKIP_INVALID_FK
        )
        
        success = migrator.migrate()
        
        if success:
            print("\n✅ Миграция завершена успешно!")
            print(f"📝 Лог: {migrator.log_file}")
            print(f"💾 БД:  {migrator.target_db}")
            
            if TEST_MODE:
                print("\n💡 СЛЕДУЮЩИЕ ШАГИ:")
                print("   1. Проверьте тестовую БД")
                print("   2. Если всё ОК: TEST_MODE = False")
                print("   3. Запустите полную миграцию")
        else:
            print("\n❌ Миграция с ошибками")
            print(f"📝 Лог: {migrator.log_file}")
    
    except Exception as e:
        print(f"\n❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()