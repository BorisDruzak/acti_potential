from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
from database_backends import DatabaseSwitcher
from database_sqlserver import SqlServerDatabaseManager
import app_config
import os


class SqlServerProfileDialog(QDialog):
    """Диалог создания профиля подключения к SQL Server."""

    def __init__(self, parent=None, create_database: bool = False):
        super().__init__(parent)
        self._profile_data = None
        self.create_database = create_database
        self.setWindowTitle("Создать новую базу SQL Server" if create_database else "Подключить базу SQL Server")
        self.setModal(True)
        self.setMinimumWidth(560)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout()
        form = QFormLayout()

        self.profile_name_edit = QLineEdit("sqlserver_new")
        self.server_edit = QLineEdit("192.168.100.11")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(1433)
        self.database_edit = QLineEdit("acti_v2")
        self.user_edit = QLineEdit("acti_user")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.files_root_edit = QLineEdit(r"\\AD-MAIN\acti_v2")
        self.storage_subdir_edit = QLineEdit("sqlserver_new")
        self.encrypt_checkbox = QCheckBox("Шифровать подключение")
        self.encrypt_checkbox.setChecked(True)
        self.trust_cert_checkbox = QCheckBox("Доверять сертификату сервера")
        self.trust_cert_checkbox.setChecked(True)
        self.effective_path_label = QLabel()
        self.effective_path_label.setWordWrap(True)
        self.effective_path_label.setStyleSheet("color: #6c757d; font-size: 9pt;")

        browse_btn = QPushButton("📂 Обзор...")
        browse_btn.clicked.connect(self.browse_files_root)
        files_root_layout = QHBoxLayout()
        files_root_layout.setContentsMargins(0, 0, 0, 0)
        files_root_layout.addWidget(self.files_root_edit, 1)
        files_root_layout.addWidget(browse_btn)
        files_root_widget = QWidget()
        files_root_widget.setLayout(files_root_layout)

        form.addRow("Имя профиля:", self.profile_name_edit)
        form.addRow("Сервер:", self.server_edit)
        form.addRow("Порт:", self.port_spin)
        form.addRow("База данных:", self.database_edit)
        form.addRow("Логин:", self.user_edit)
        form.addRow("Пароль:", self.password_edit)
        form.addRow("Папка файлов на сервере:", files_root_widget)
        form.addRow("Подпапка базы:", self.storage_subdir_edit)
        form.addRow("Итоговый путь файлов:", self.effective_path_label)
        form.addRow("", self.encrypt_checkbox)
        form.addRow("", self.trust_cert_checkbox)

        help_label = QLabel(
            "Здесь указывается путь к папке файлов на сервере. Для рабочих мест обычно нужен "
            "сетевой путь вида \\\\SERVER\\share, а не локальный путь клиента. Если у нескольких "
            "баз один общий корень, используйте разные подпапки базы, чтобы файлы не смешивались."
        )
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color: #6c757d; font-size: 9pt;")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        test_btn = QPushButton("🔌 Проверить подключение")
        buttons.addButton(test_btn, QDialogButtonBox.ActionRole)
        save_btn = buttons.button(QDialogButtonBox.Save)
        if save_btn:
            save_btn.setText("Сохранить")
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        if cancel_btn:
            cancel_btn.setText("Отмена")
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        test_btn.clicked.connect(self.test_connection)
        self.profile_name_edit.textChanged.connect(self._sync_storage_subdir_with_profile_name)
        self.files_root_edit.textChanged.connect(self.update_effective_path_preview)
        self.storage_subdir_edit.textChanged.connect(self.update_effective_path_preview)

        layout.addLayout(form)
        layout.addWidget(help_label)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self.update_effective_path_preview()

    def browse_files_root(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Выберите корень файлового хранилища",
            self.files_root_edit.text().strip() or r"\\AD-MAIN\acti_v2",
            QFileDialog.ShowDirsOnly,
        )
        if folder:
            self.files_root_edit.setText(folder)

    @staticmethod
    def _sanitize_storage_subdir(value: str) -> str:
        cleaned = (value or "").strip()
        return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in cleaned) or "default"

    def _sync_storage_subdir_with_profile_name(self, value: str):
        expected = self._sanitize_storage_subdir(value)
        current = self.storage_subdir_edit.text().strip()
        if not current or current == "sqlserver_new":
            self.storage_subdir_edit.setText(expected)
        self.update_effective_path_preview()

    def update_effective_path_preview(self):
        root = self.files_root_edit.text().strip()
        subdir = self._sanitize_storage_subdir(self.storage_subdir_edit.text().strip())
        effective = os.path.join(root, subdir) if root else subdir
        self.effective_path_label.setText(effective)

    def _collect_profile_data(self) -> dict:
        profile_name = self.profile_name_edit.text().strip()
        server = self.server_edit.text().strip()
        database = self.database_edit.text().strip()
        user = self.user_edit.text().strip()
        password = self.password_edit.text()
        files_root = self.files_root_edit.text().strip()
        storage_subdir = self._sanitize_storage_subdir(self.storage_subdir_edit.text().strip())

        if not profile_name:
            raise ValueError("Укажите имя профиля.")
        if not server:
            raise ValueError("Укажите сервер.")
        if not database:
            raise ValueError("Укажите имя базы данных.")
        if not user:
            raise ValueError("Укажите логин.")
        if not password:
            raise ValueError("Укажите пароль.")

        if not files_root:
            raise ValueError("Укажите корень файлового хранилища.")

        return {
            "name": profile_name,
            "server": server,
            "port": int(self.port_spin.value()),
            "database": database,
            "user": user,
            "password": password,
            "files_root": files_root,
            "storage_subdir": storage_subdir,
            "encrypt": self.encrypt_checkbox.isChecked(),
            "trust_server_certificate": self.trust_cert_checkbox.isChecked(),
            "create_database": self.create_database,
        }

    def test_connection(self, show_success: bool = True) -> bool:
        try:
            data = self._collect_profile_data()
            config = {
                "profile_name": data["name"],
                "server": data["server"],
                "port": data["port"],
                "database": data["database"],
                "user": data["user"],
                "password": data["password"],
                "files_root": data["files_root"],
                "storage_subdir": data["storage_subdir"],
                "encrypt": data["encrypt"],
                "trust_server_certificate": data["trust_server_certificate"],
                "initialize_schema": bool(data.get("create_database")),
            }
            if data.get("create_database"):
                SqlServerDatabaseManager.ensure_database_exists(config)
            manager = SqlServerDatabaseManager(
                config,
                create_if_not_exists=bool(data.get("create_database")),
            )
            manager.close()
            if show_success:
                success_text = (
                    "База SQL Server создана и подключение успешно проверено."
                    if data.get("create_database")
                    else "Подключение к SQL Server успешно проверено."
                )
                QMessageBox.information(self, "Подключение", success_text)
            return True
        except Exception as e:
            QMessageBox.critical(self, "Ошибка подключения", f"Не удалось подключиться к SQL Server:\n\n{e}")
            return False

    def validate_and_accept(self):
        try:
            if not self.test_connection(show_success=False):
                return
            self._profile_data = self._collect_profile_data()
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "Проверка данных", str(e))

    def get_profile_data(self):
        return dict(self._profile_data or {})


class DatabaseSwitcherDialog(QDialog):
    """Диалог для переключения между базами данных"""
    
    database_changed = pyqtSignal(str, object)  # Сигнал: (имя БД, новый менеджер)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.switcher = DatabaseSwitcher()
        self.init_ui()
        self.load_databases()
    
    def init_ui(self):
        """Инициализация интерфейса"""
        self.setWindowTitle("Управление профилями данных")
        self.setMinimumSize(700, 500)
        self.setModal(True)
        
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(20, 20, 20, 20)
        
        # Заголовок
        header = QLabel("🗄️ Управление профилями данных")
        header.setStyleSheet("""
            QLabel {
                font-size: 18pt;
                font-weight: bold;
                color: #2c3e50;
                padding: 10px;
            }
        """)
        
        # Таблица с базами данных
        self.databases_table = QTableWidget()
        self.databases_table.setColumnCount(4)
        self.databases_table.setHorizontalHeaderLabels(
            ["Профиль", "Подключение / путь", "Дата добавления", "Действия"]
        )
        self.databases_table.horizontalHeader().setStretchLastSection(False)
        self.databases_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.databases_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.databases_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.databases_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.databases_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.databases_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.databases_table.setAlternatingRowColors(True)
        self.databases_table.setStyleSheet("""
            QTableWidget {
                gridline-color: #e0e0e0;
                background-color: white;
                selection-background-color: #3498db;
                selection-color: white;
            }
            QHeaderView::section {
                background-color: #34495e;
                color: white;
                font-weight: bold;
                padding: 8px;
                border: none;
            }
        """)
        
        # Панель управления
        controls_layout = QHBoxLayout()
        
        # Кнопка добавления существующей БД
        self.add_existing_btn = QPushButton("🗂 Локальная база SQLite")
        self.add_existing_btn.clicked.connect(self.show_sqlite_menu)
        self.add_existing_btn.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                padding: 10px 20px;
                border: none;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
        """)
        
        # Кнопка создания нового профиля
        self.create_new_btn = QPushButton("🖧 Серверная база SQL Server")
        self.create_new_btn.clicked.connect(self.show_sqlserver_menu)
        self.create_new_btn.setStyleSheet("""
            QPushButton {
                background-color: #2ecc71;
                color: white;
                padding: 10px 20px;
                border: none;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #27ae60;
            }
        """)
        
        # Кнопка удаления из списка
        self.remove_btn = QPushButton("🗑️ Удалить из списка")
        self.remove_btn.clicked.connect(self.remove_database)
        self.remove_btn.setEnabled(False)
        self.remove_btn.setStyleSheet("""
            QPushButton {
                background-color: #e74c3c;
                color: white;
                padding: 10px 20px;
                border: none;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #c0392b;
            }
            QPushButton:disabled {
                background-color: #cccccc;
                color: #999999;
            }
        """)
        
        controls_layout.addWidget(self.add_existing_btn)
        controls_layout.addWidget(self.create_new_btn)
        controls_layout.addStretch()
        controls_layout.addWidget(self.remove_btn)
        
        # Текущая БД
        self.current_db_label = QLabel("Текущий профиль: не выбран")
        self.current_db_label.setStyleSheet("""
            QLabel {
                background-color: #ecf0f1;
                padding: 10px;
                border-radius: 5px;
                font-weight: bold;
                color: #2c3e50;
            }
        """)
        
        # Кнопки диалога
        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        close_button = button_box.button(QDialogButtonBox.Close)
        if close_button:
            close_button.setText("Закрыть")
        button_box.rejected.connect(self.reject)
        
        # Сборка
        main_layout.addWidget(header)
        main_layout.addWidget(self.current_db_label)
        main_layout.addWidget(self.databases_table, 1)
        main_layout.addLayout(controls_layout)
        main_layout.addWidget(button_box)
        
        self.setLayout(main_layout)
        
        # Подключаем сигналы
        self.databases_table.itemSelectionChanged.connect(self.on_selection_changed)
    
    def load_databases(self):
        """Загрузить список профилей с визуальным выделением активного"""
        self.databases_table.setRowCount(0)
        
        databases = self.switcher.get_database_list()
        current_db = self.switcher.current_db
        
        for db in databases:
            row = self.databases_table.rowCount()
            self.databases_table.insertRow(row)
            
            is_active = (db['name'] == current_db)
            
            backend = str(db.get("backend") or "sqlite").upper()
            name_text = f"[{backend}] {db['name']}"
            if is_active:
                name_text = f"⭐ {name_text} (АКТИВНА)"
             
            name_item = QTableWidgetItem(name_text)
            name_item.setData(Qt.UserRole, db["name"])
            name_item.setFont(QFont("Arial", 10, QFont.Bold if is_active else QFont.Normal))
            
            # НОВОЕ: Визуальное выделение активной БД
            if is_active:
                from PyQt5.QtGui import QColor, QBrush
                # Зеленый фон для активной БД
                name_item.setBackground(QBrush(QColor(212, 237, 218)))  # Светло-зеленый
                name_item.setForeground(QBrush(QColor(21, 87, 36)))     # Темно-зеленый текст
            
            self.databases_table.setItem(row, 0, name_item)
            
            # Путь
            path_item = QTableWidgetItem(db['path'])
            if is_active:
                path_item.setBackground(QBrush(QColor(212, 237, 218)))
            self.databases_table.setItem(row, 1, path_item)
            
            # Дата добавления
            date_item = QTableWidgetItem(db.get('added', 'Неизвестно'))
            if is_active:
                date_item.setBackground(QBrush(QColor(212, 237, 218)))
            self.databases_table.setItem(row, 2, date_item)
            
            # Кнопка переключения
            if is_active:
                # Для активной БД показываем статус
                status_btn = QPushButton("✅ Активна")
                status_btn.setEnabled(False)
                status_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #27ae60;
                        color: white;
                        padding: 5px 10px;
                        border: none;
                        border-radius: 3px;
                        font-weight: bold;
                    }
                    QPushButton:disabled {
                        background-color: #27ae60;
                        color: white;
                    }
                """)
                self.databases_table.setCellWidget(row, 3, status_btn)
            else:
                # Для неактивных - кнопка переключения
                switch_btn = QPushButton("⚡ Переключить")
                switch_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #9b59b6;
                        color: white;
                        padding: 5px 10px;
                        border: none;
                        border-radius: 3px;
                    }
                    QPushButton:hover {
                        background-color: #8e44ad;
                    }
                """)
                switch_btn.clicked.connect(
                    lambda checked, name=db['name']: self.switch_database(name)
                )
                self.databases_table.setCellWidget(row, 3, switch_btn)
        
        # Обновляем текущую БД с эмодзи
        if current_db:
            active_profile = self.switcher.databases.get(current_db, {})
            active_backend = str(active_profile.get("backend") or "sqlite").upper()
            self.current_db_label.setText(f"⭐ Текущий профиль: [{active_backend}] {current_db}")
            self.current_db_label.setStyleSheet("""
                QLabel {
                    background-color: #d4edda;
                    padding: 10px;
                    border-radius: 5px;
                    font-weight: bold;
                    color: #155724;
                    border: 2px solid #28a745;
                }
            """)
        else:
            self.current_db_label.setText("⚠️ Текущий профиль: не выбран")
            self.current_db_label.setStyleSheet("""
                QLabel {
                    background-color: #fff3cd;
                    padding: 10px;
                    border-radius: 5px;
                    font-weight: bold;
                    color: #856404;
                    border: 2px solid #ffc107;
                }
            """)

    def show_sqlite_menu(self):
        menu = QMenu(self)
        menu.addAction("Подключить существующую SQLite базу", self.add_existing_database)
        menu.addAction("Создать новую SQLite базу", self.create_new_database)
        menu.exec_(self.add_existing_btn.mapToGlobal(self.add_existing_btn.rect().bottomLeft()))

    def show_sqlserver_menu(self):
        menu = QMenu(self)
        menu.addAction("Подключить существующую базу SQL Server", lambda: self.add_sqlserver_profile(False))
        menu.addAction("Создать новую базу SQL Server", lambda: self.add_sqlserver_profile(True))
        menu.exec_(self.create_new_btn.mapToGlobal(self.create_new_btn.rect().bottomLeft()))

    def show_create_profile_menu(self):
        backend_labels = [
            "SQLite (локальная база)",
            "SQL Server (серверный профиль)",
        ]
        selected, ok = QInputDialog.getItem(
            self,
            "Новый профиль",
            "Выберите тип профиля:",
            backend_labels,
            0,
            False,
        )
        if not ok or not selected:
            return

        if selected.startswith("SQLite"):
            self.create_new_database()
        else:
            self.add_sqlserver_profile()

    def add_sqlserver_profile(self, create_database: bool = False):
        dialog = SqlServerProfileDialog(self, create_database=create_database)
        if dialog.exec_() != QDialog.Accepted:
            return

        profile = dialog.get_profile_data()
        if not profile:
            return

        profile_name = profile["name"]
        if profile_name in self.switcher.databases:
            reply = QMessageBox.question(
                self,
                "Профиль уже существует",
                f"Профиль '{profile_name}' уже есть в списке.\nПерезаписать его?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        self.switcher.add_sqlserver_profile(
            name=profile_name,
            server=profile["server"],
            database=profile["database"],
            user=profile["user"],
            port=profile["port"],
            files_root=profile["files_root"],
            storage_subdir=profile["storage_subdir"],
            password=profile["password"],
            encrypt=profile["encrypt"],
            trust_server_certificate=profile["trust_server_certificate"],
        )
        self.load_databases()

        message_text = (
            f"Создана новая база SQL Server и добавлен профиль '{profile_name}'.\nПодключиться к нему сейчас?"
            if create_database
            else f"Профиль SQL Server '{profile_name}' добавлен.\nПодключиться к нему сейчас?"
        )
        reply = QMessageBox.question(
            self,
            "Профиль SQL Server сохранен",
            message_text,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply == QMessageBox.Yes:
            self.switch_database(profile_name)

    def create_database_structure(self, db_name: str, db_source_path: str = None) -> str:
        """
        Создать файловую структуру для базы данных
        
        Args:
            db_name: Название базы данных
            db_source_path: Путь к исходному файлу БД (для копирования) или None (для создания новой)
        
        Returns:
            str: Путь к созданной/скопированной БД в новой структуре
        
        Создает структуру:
            data/
            └── (db_name)/
                ├── database.db    (скопированная или новая БД)
                └── files/         (папка для документов)
        """
        try:
            # 1. Определяем базовые пути
            app_dir = app_config.get_app_root()  # Папка с exe файлом
            data_root = os.path.join(app_dir, "data")
            db_folder = os.path.join(data_root, db_name)
            db_file_path = os.path.join(db_folder, "database.db")
            files_folder = os.path.join(db_folder, "files")
            
            # 2. Создаем папки
            print(f"📁 Создание структуры для БД '{db_name}'...")
            os.makedirs(data_root, exist_ok=True)
            os.makedirs(db_folder, exist_ok=True)
            os.makedirs(files_folder, exist_ok=True)
            
            # 3. Работа с файлом БД
            if db_source_path:
                # Копируем существующую БД
                print(f"📋 Копирование БД из: {db_source_path}")
                print(f"📋 Копирование БД в: {db_file_path}")
                
                if os.path.exists(db_file_path):
                    # Если файл уже есть - спрашиваем перезаписать
                    reply = QMessageBox.question(
                        None,
                        "Перезаписать БД?",
                        f"База данных '{db_name}' уже существует.\nПерезаписать?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No
                    )
                    
                    if reply == QMessageBox.No:
                        raise Exception("Отменено пользователем")
                    
                    # Удаляем старую БД
                    os.remove(db_file_path)
                
                # Копируем файл БД
                import shutil
                shutil.copy2(db_source_path, db_file_path)
                print(f"✅ БД скопирована")
            else:
                # Создаем новую пустую БД
                print(f"🆕 Создание новой БД")
                from database_optimized import DatabaseManager
                new_manager = DatabaseManager(db_file_path, create_if_not_exists=True)
                new_manager.close()
                print(f"✅ Новая БД создана")
            
            print(f"✅ Структура создана:")
            print(f"   📁 Папка БД: {db_folder}")
            print(f"   📄 Файл БД: {db_file_path}")
            print(f"   📂 Папка файлов: {files_folder}")
            
            return db_file_path
            
        except Exception as e:
            print(f"❌ Ошибка создания структуры: {e}")
            raise    
    def add_existing_database(self):
        """Добавить существующую SQLite БД с проверкой схемы и созданием структуры"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите файл базы данных",
            "",
            "База SQLite (*.db);;Все файлы (*)"
        )
        
        if not file_path:
            return
        
        try:
            # 1. Проверяем схему БД
            is_valid, message = self.switcher.validate_database_schema(file_path)
            
            if not is_valid:
                reply = QMessageBox.warning(
                    self,
                    "⚠️ Некорректная схема БД",
                    f"Выбранная база данных имеет проблемы:\n\n{message}\n\n"
                    f"Добавить её в список всё равно?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )
                
                if reply == QMessageBox.No:
                    return
            
            # 2. Запрашиваем название
            name, ok = QInputDialog.getText(
                self,
                "Название базы данных",
                "Введите название для этой БД:"
            )
            
            if not ok or not name:
                return
            
            # 🆕 3. Создаем файловую структуру и копируем БД
            progress = QProgressDialog(
                "Создание файловой структуры и копирование БД...",
                "Отмена",
                0, 0,
                self
            )
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.show()
            QApplication.processEvents()
            
            try:
                # Создаем структуру и копируем БД
                new_db_path = self.create_database_structure(name, file_path)
                
                # 4. Добавляем в конфигурацию
                self.switcher.add_database(name, new_db_path)
                
                progress.close()
                
                # 5. Обновляем интерфейс
                self.load_databases()
                
                # 6. Сообщаем результат
                if is_valid:
                    QMessageBox.information(
                        self,
                        "✅ Успешно",
                        f"SQLite база данных '{name}' успешно добавлена!\n\n"
                        f"📁 Структура создана в: data/{name}/\n"
                        f"📄 БД: data/{name}/database.db\n"
                        f"📂 Файлы: data/{name}/files/\n\n"
                        f"{message}"
                    )
                else:
                    QMessageBox.warning(
                        self,
                        "⚠️ БД добавлена с предупреждением",
                        f"SQLite база данных '{name}' добавлена, но имеет проблемы:\n\n{message}"
                    )
                    
            except Exception as e:
                progress.close()
                raise e
                
        except Exception as e:
            QMessageBox.critical(
                self,
                "❌ Ошибка",
                f"Не удалось добавить SQLite БД: {str(e)}"
            )
    
    def create_new_database(self):
        """Создать новую SQLite БД с правильной структурой папок"""
        # 1. Запрашиваем название
        name, ok = QInputDialog.getText(
            self,
            "Новая SQLite база данных",
            "Введите название для новой SQLite БД:"
        )
        
        if not ok or not name:
            return
        
        try:
            # 🆕 2. Создаем структуру БД (без исходного файла)
            progress = QProgressDialog(
                "Создание новой SQLite базы данных...",
                "Отмена",
                0, 0,
                self
            )
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.show()
            QApplication.processEvents()
            
            try:
                # Создаем структуру и новую пустую БД
                new_db_path = self.create_database_structure(name, db_source_path=None)
                
                # 3. Добавляем в конфигурацию
                self.switcher.add_database(name, new_db_path)
                
                progress.close()
                
                # 4. Обновляем интерфейс
                self.load_databases()
                
                # 5. Сообщаем результат
                QMessageBox.information(
                    self,
                    "✅ Успешно",
                    f"Новая SQLite база данных '{name}' создана!\n\n"
                    f"📁 Структура:\n"
                    f"   data/{name}/\n"
                    f"   ├── database.db\n"
                    f"   └── files/"
                )
                
            except Exception as e:
                progress.close()
                raise e
                
        except Exception as e:
            QMessageBox.critical(
                self,
                "❌ Ошибка",
                f"Не удалось создать БД: {str(e)}"
            )
    
    def switch_database(self, name: str):
        """Переключиться на выбранную БД с валидацией"""
        try:
            reply = QMessageBox.question(
                self,
                "Подтверждение",
                f"Переключиться на профиль '{name}'?\n\n"
                "Текущие несохраненные изменения будут потеряны.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                # Показываем прогресс
                progress = QProgressDialog(
                    "Проверка и подключение к базе данных...",
                    "Отмена",
                    0, 0,
                    self
                )
                progress.setWindowModality(Qt.WindowModal)
                progress.setMinimumDuration(0)
                progress.show()
                QApplication.processEvents()
                
                try:
                    # ИЗМЕНЕНИЕ: Получаем новый менеджер (с валидацией внутри)
                    new_manager = self.switcher.switch_database(name)
                    
                    # НОВОЕ: Сохраняем ссылку на новый менеджер
                    self.new_manager = new_manager
            
                    progress.close()
            
            # Обновляем интерфейс
                    self.load_databases()  # Перерисовываем таблицу с новым выделением
            
            # Эмитим сигнал с новым менеджером
                    self.database_changed.emit(name, new_manager)
                    
                    QMessageBox.information(
                        self,
                        "✅ Успешно",
                        f"Переключено на профиль '{name}'.\n\n"
                        f"Подключение и схема backend проверены."
                    )
                    
                except Exception as e:
                    progress.close()
                    raise e
                    
        except Exception as e:
            QMessageBox.critical(
                self,
                "❌ Ошибка",
                f"Не удалось переключить профиль:\n\n{str(e)}\n\n"
                f"Проверьте параметры подключения или структуру базы данных."
            )
            print(f"❌ Ошибка переключения профиля: {e}")
    
    def remove_database(self):
        """Удалить профиль из списка"""
        selected_rows = self.databases_table.selectionModel().selectedRows()
        
        if not selected_rows:
            return
        
        row = selected_rows[0].row()
        name_item = self.databases_table.item(row, 0)
        name = name_item.data(Qt.UserRole) if name_item else None
        if not name:
            return
        backend = str(self.switcher.databases.get(name, {}).get("backend") or "sqlite").lower()
        target_description = (
            "Подключение к SQL Server на сервере затронуто не будет."
            if backend == "sqlserver"
            else "Файл базы данных не будет удален с диска."
        )
        
        reply = QMessageBox.question(
            self,
            "Подтверждение",
            f"Удалить профиль '{name}' из списка?\n\n"
            f"{target_description}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            if name in self.switcher.databases:
                del self.switcher.databases[name]
                self.switcher.save_config()
                self.load_databases()
                
                QMessageBox.information(
                    self,
                    "Успешно",
                    f"Профиль '{name}' удален из списка"
                )
    
    def on_selection_changed(self):
        """Обработчик изменения выбора"""
        has_selection = len(self.databases_table.selectionModel().selectedRows()) > 0
        self.remove_btn.setEnabled(has_selection)
