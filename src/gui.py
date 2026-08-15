from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QModelIndex, QAbstractTableModel, QDate, QSettings, QByteArray, QTimer
from PyQt5.QtGui import QColor, QFont
from documents_table import DocumentsTableView
from typing import Any

from metadata_form import MetadataEditor
from work_with_word_docs import WordDocumentHandler
from add_document_dialog import AddDocumentDialog
import time
from reference_manager_dialog import ReferenceManagerDialog
from compact_metadata_editor import CompactMetadataEditor
from datetime import datetime
from advensed_search import AdvancedSearchDialog
from export_manager import show_export_dialog
from ui_styles import AppColors, AppStyles, AppLayout
from app_diagnostics import DiagnosticsDialog, get_diagnostics_manager, run_diagnostics_checks
from database_switcher_dialog import DatabaseSwitcherDialog
from quick_preview_dialog import QuickPreviewDialog
from document_utils import format_date_for_display
import os  #  для функции открытия файлов
import subprocess  # для функции открытия файлов
import platform  
import json
import logging
from tag_search_widget import SimpleTagSearchWidget
class CompactDocumentsTableModel(QAbstractTableModel):
    """Упрощенная модель таблицы для боковой панели"""
    
    def __init__(self, data, headers):
        super().__init__()
        self._data = data
        self._headers = headers
    
    def rowCount(self, parent=QModelIndex()):
        return len(self._data) if self._data else 0
    
    def columnCount(self, parent=QModelIndex()):
        return len(self._headers)
    
    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not self._data:
            return None
            
        row = index.row()
        col = index.column()
        
        if row >= len(self._data) or col >= len(self._data[row]):
            return None
        
        value = self._data[row][col]
        
        if role == Qt.DisplayRole:
            if col == 2 and value:  # Дата
                return format_date_for_display(value)
            
            if col == 0 and isinstance(value, str) and len(value) > 25:  # title
                return value[:22] + "..."
            
            return str(value) if value is not None else "-"
        
        elif role == Qt.BackgroundRole:
            if row % 2 == 0:
                return QColor(248, 249, 250)
            return QColor(255, 255, 255)
        
        elif role == Qt.ForegroundRole:
            if col == 0:  # title
                return QColor(52, 73, 94)
            return QColor(44, 62, 80)
        
        elif role == Qt.ToolTipRole:
            if col == 0:
                return f"Документ: {value}"
            elif col == 1:
                return f"Рег. номер: {value}"
            elif col == 2:
                return f"Дата: {value}"
            
        return None
    
    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if section < len(self._headers):
                return self._headers[section]
        return None
    
    def flags(self, index):
        """Запрещаем редактирование"""
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable
    
    def update_data(self, new_data):
        self.beginResetModel()
        self._data = new_data
        self.endResetModel()
    

class CompactDocumentsTableView(QTableView):
    """Упрощенная таблица документов для боковой панели"""
    
    document_selected = pyqtSignal(int)  # document_id
    document_preview_requested = pyqtSignal(int)  # document_id для предпросмотра
    show_metadata_tab = pyqtSignal(int)  # Для открытия редактора
    open_file_requested = pyqtSignal(int) 
    def __init__(self, db_manager):
        super().__init__()
        self.db_manager = db_manager
        self.setup_table()
        self.load_documents()
    
    def setup_table(self):
        """Настройка компактной таблицы"""
        self.setSortingEnabled(True)
        self.setAlternatingRowColors(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setShowGrid(True)
        self.verticalHeader().setVisible(False)
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet(AppStyles.table_view())
        
        self.clicked.connect(self.on_cell_clicked)
        self.doubleClicked.connect(self.on_cell_double_clicked)

    @staticmethod
    def _format_table_data(documents):
        table_data = []
        for doc in documents or []:
            table_data.append([
                doc.get("title") or "Без названия",
                doc.get("reg_number") or "-",
                doc.get("reg_date") or "-",
                doc.get("id"),
            ])
        return table_data
    
    def load_documents(self, filters=None):
        """Загрузка документов из БД"""
        try:
            if filters:
                limit = filters.get("limit", 100) if filters.get("load_recent") else 1000
                documents = self.db_manager.get_compact_documents(filters=filters, limit=limit)
            else:
                documents = self.db_manager.get_compact_documents(limit=100)

            table_data = self._format_table_data(documents)
            self.update_data(table_data)
            print(f"✅ Загружено документов в компактную таблицу: {len(table_data)}")
            
        except Exception as e:
            print(f"❌ Ошибка загрузки документов в компактную таблицу: {e}")
            import traceback
            traceback.print_exc()
        
    def on_cell_clicked(self, index):
        """Обработчик клика - показываем метаданные в компактном редакторе"""
        try:
            row = index.row()
            # Берем ID из позиции [3]
            doc_id = self.model()._data[row][3]
            self.document_selected.emit(doc_id)
            print(f"📋 Выбран документ из компактного списка ID: {doc_id}")
        except Exception as e:
            print(f"❌ Ошибка при клике в компактной таблице: {e}")
            import traceback
            traceback.print_exc()
    
    def on_cell_double_clicked(self, index):
        """Обработчик двойного клика - открываем редактор метаданных"""
        try:
            row = index.row()
            # Берем ID из позиции [3]
            doc_id = self.model()._data[row][3]
            # Открываем редактор метаданных, а не предпросмотр
            self.show_metadata_tab.emit(doc_id)
            print(f"🎯 Двойной клик: открываем редактор для документа ID {doc_id}")
        except Exception as e:
            print(f"❌ Ошибка при двойном клике: {e}")
            import traceback
            traceback.print_exc()
    def update_data_with_filter(self, filtered_data):
        """Обновление данных таблицы с фильтрацией"""
        if not hasattr(self, 'model_instance'):
            headers = ["Название", "Рег. номер", "Дата", "ID"]
            self.model_instance = CompactDocumentsTableModel(filtered_data, headers)
            self.setModel(self.model_instance)
        else:
            self.model_instance.update_data(filtered_data)
        
        # Настройка размеров колонок
        self.setColumnWidth(0, 150)  # Название
        self.setColumnWidth(1, 80)   # Рег. номер
        self.setColumnWidth(2, 70)   # Дата
        self.setColumnWidth(3, 40)   # ID
        
        # Принудительное обновление отображения
        self.viewport().update()
    def contextMenuEvent(self, event):
        """Контекстное меню с поддержкой режима быстрого доступа"""
        try:
            index = self.indexAt(event.pos())
            if not index.isValid():
                return
            
            menu = QMenu(self)
            menu.setStyleSheet(AppStyles.menu())
            
            row = index.row()
            doc_id = self.model()._data[row][3]  # ID теперь в конце
            doc_title = self.model()._data[row][0]  # Название первое
            reg_number = self.model()._data[row][1]  # Рег номер
            
            # Получаем главное окно для проверки режима
            main_window = self.window()
            is_quick_access_mode = getattr(main_window, 'current_compact_mode', 0) == 1
            
            # Предпросмотр
            preview_action = QAction("👁️ Предпросмотр", self)
            preview_action.triggered.connect(
                lambda checked=False: self.document_preview_requested.emit(doc_id))
            
            # Редактировать метаданные
            metadata_action = QAction("📋 Редактировать метаданные", self)
            metadata_action.triggered.connect(
                lambda checked=False: self.show_metadata_tab.emit(doc_id))
            
            # Открыть в Word/LibreOffice
            open_file_action = QAction("📂 Открыть в Word", self)
            open_file_action.triggered.connect(
                lambda checked=False: self.open_file_requested.emit(doc_id))
            
            # Копировать рег номер
            copy_reg_action = QAction(f"📄 Копировать рег. номер: {reg_number}", self)
            copy_reg_action.triggered.connect(
                lambda checked=False: QApplication.clipboard().setText(str(reg_number)))
            
            # Копировать название
            copy_title_action = QAction("📝 Копировать название", self)
            copy_title_action.triggered.connect(
                lambda checked=False: QApplication.clipboard().setText(doc_title))
            
            # Добавляем основные пункты
            menu.addAction(preview_action)
            menu.addAction(metadata_action)
            menu.addAction(open_file_action)
            menu.addSeparator()
            menu.addAction(copy_reg_action)
            menu.addAction(copy_title_action)
            
            # ✅ НОВОЕ: Добавляем специальные пункты для режима быстрого доступа
            if is_quick_access_mode:
                menu.addSeparator()
                
                # Удалить из быстрого доступа
                remove_action = QAction("🗑️ Удалить из быстрого доступа", self)
                remove_action.triggered.connect(
                    lambda checked=False: self.remove_from_quick_access_action(doc_id))
                
                # Очистить весь список
                clear_all_action = QAction("🧹 Очистить весь список", self)
                clear_all_action.triggered.connect(
                    lambda checked=False: self.clear_quick_access_action())
                
                # Экспортировать документы
                export_action = QAction("📤 Экспортировать документы", self)
                export_action.triggered.connect(
                    lambda checked=False: self.export_quick_access_action())
                
                menu.addAction(remove_action)
                menu.addAction(clear_all_action)
                menu.addSeparator()
                menu.addAction(export_action)
            
            menu.exec_(event.globalPos())
            
        except Exception as e:
            print(f"❌ Ошибка контекстного меню: {e}")
            import traceback
            traceback.print_exc()

    def remove_from_quick_access_action(self, doc_id):
        """Удалить документ из быстрого доступа"""
        try:
            main_window = self.window()
            if hasattr(main_window, 'remove_from_quick_access'):
                main_window.remove_from_quick_access(doc_id)
        except Exception as e:
            print(f"❌ Ошибка удаления из быстрого доступа: {e}")

    def clear_quick_access_action(self):
        """Очистить весь список быстрого доступа"""
        try:
            main_window = self.window()
            if hasattr(main_window, 'clear_quick_access'):
                main_window.clear_quick_access()
        except Exception as e:
            print(f"❌ Ошибка очистки быстрого доступа: {e}")

    def export_quick_access_action(self):
        """Экспортировать документы из быстрого доступа"""
        try:
            main_window = self.window()
            if hasattr(main_window, 'export_quick_access_documents'):
                main_window.export_quick_access_documents()
        except Exception as e:
            print(f"❌ Ошибка экспорта: {e}")

    def update_data(self, table_data):
        """Обновление данных в таблице"""
        headers = ["Название", "Рег. номер", "Дата", "ID"]
        
        if not hasattr(self, 'model_instance'):
            self.model_instance = CompactDocumentsTableModel(table_data, headers)
            self.setModel(self.model_instance)
        else:
            self.model_instance.update_data(table_data)
        
        # Настройка размеров колонок
        self.setColumnWidth(0, 200)   # ID
        self.setColumnWidth(1, 100)  # Название
        self.setColumnWidth(2, 90)   # Рег. номер
        self.setColumnWidth(3, 75)   # Дата

class DocumentLoader(QThread):
    """Асинхронная загрузка Рё обработка документов"""
    
    # Сигналы для коммуникации с UI
    text_loaded = pyqtSignal(str, str)  # текст, имя файла
    loading_progress = pyqtSignal(str)  # статус загрузки
    error_occurred = pyqtSignal(str)    # ошибка
    
    def __init__(self, document_handler, filename, max_chars=15000):
        super().__init__()
        self.document_handler = document_handler
        self.filename = filename
        self.max_chars = max_chars
        self._is_cancelled = False
    
    def cancel(self):
        """Отменить загрузку"""
        self._is_cancelled = True
        self.loading_progress.emit("❌ Загрузка отменена")
    
    def run(self):
        """Основной поток загрузки"""
        try:
            if self._is_cancelled:
                return
                
            self.loading_progress.emit(f"📖 Читаем {self.filename}...")
            
            # Небольшая задержка для плавности UI
            self.msleep(100)
            
            if self._is_cancelled:
                return
            
            # РР·РІР»РµРєР°РµРј текст
            start_time = time.time()
            text = self.document_handler.extract_text(self.filename)
            load_time = time.time() - start_time
            
            if self._is_cancelled:
                return
            
            self.loading_progress.emit("⚡ Обрабатываем текст...")
            
            # Обрабатываем текст
            if not text or not text.strip():
                text = "📄 Документ пуст или не содержит читаемого текста"
            else:
                # Ограничиваем размер для производительности
                original_length = len(text)
                if len(text) > self.max_chars:
                    text = text[:self.max_chars]
                    text += f"\n\n{'='*60}\n📊 СТАТИСТИКА ПРЕДПРОСМОТРА:\n"
                    text += f"• Показано: {self.max_chars:,} символов\n"
                    text += f"• Всего в документе: {original_length:,} символов\n"
                    text += f"• Скрыто: {original_length - self.max_chars:,} символов\n"
                    text += f"• Время загрузки: {load_time:.2f} сек\n"
                    text += f"{'='*60}"
            
            if self._is_cancelled:
                return
            
            # Финальная обработка
            text = text.replace('\r\n', '\n').replace('\r', '\n')
            
            # Отправляем результат
            self.text_loaded.emit(text, self.filename)
            
        except Exception as e:
            if not self._is_cancelled:
                error_msg = f"❌ Ошибка при загрузке '{self.filename}':\n\n{str(e)}"
                self.error_occurred.emit(error_msg)


class MainWindow(QMainWindow):
    def __init__(self, db_manager: Any, document_handler: WordDocumentHandler, switcher=None):
        super().__init__()
        self.db_manager = db_manager
        self.document_handler = document_handler
        self.settings = QSettings("ActiV2", "ActiV2")
        self.quick_access_documents = []  # Список ID документов в быстром доступе
        self.current_compact_mode = 0  # 0 = Список документов, 1 = Быстрый доступ
        #self.show_reference_manager = ReferenceManagerDialog
        # Новые переменные для асинхронной загрузки
        self.document_loader = None
        self.current_document_id = None
        self.document_cache = {}  # Кэш для быстрого повторного просмотра
        self._compact_list_cache = []
        self.saved_searches = {}
        self._background_operations = {}
        self.switcher = switcher
        self.logger = logging.getLogger("acti.gui")
        self.diagnostics_manager = get_diagnostics_manager()
        self.diagnostics_dialog = None
        self.diagnostics_alert_visible = True
        self.diagnostics_blink_timer = QTimer(self)
        self.diagnostics_blink_timer.setInterval(500)
        self.diagnostics_blink_timer.timeout.connect(self._toggle_diagnostics_alert)
        self.live_sync_timer = QTimer(self)
        self.live_sync_timer.setInterval(5000)
        self.live_sync_timer.timeout.connect(self.run_live_sync)
        self._live_sync_in_progress = False
        self._last_remote_missing_document_id = None
        self.diagnostics_manager.status_changed.connect(self._on_diagnostics_status_changed)
        self.initUI()
        self.restore_ui_state()
        QTimer.singleShot(250, self.run_diagnostics_check)
        self.live_sync_timer.start()
    def open_file_in_default_app(self, file_path):
        """
        Открыть файл в программе по умолчанию (Word, LibreOffice Рё т.д.)
        
        :param file_path: Полный путь к файлу
        """
        try:
            if not os.path.exists(file_path):
                QMessageBox.critical(
                    self,
                    "❌ Файл не найден",
                    f"Файл не существует:\n{file_path}"
                )
                return
            
            # Определяем операционную систему
            system = platform.system()
            
            if system == 'Windows':
                # Windows: используем os.startfile
                os.startfile(file_path)
            elif system == 'Darwin':
                # macOS: используем open
                subprocess.call(['open', file_path])
            else:
                # Linux: используем xdg-open
                subprocess.call(['xdg-open', file_path])
            
            print(f"✅ Файл открыт: {file_path}")
            
        except Exception as e:
            QMessageBox.critical(
                self,
                "❌ Ошибка открытия файла",
                f"Не удалось открыть файл:\n\n{str(e)}"
            )
            print(f"❌ Ошибка открытия файла: {e}")
    def initUI(self):
        self.setWindowTitle("Документы приложения")
        
        # Получаем размер экрана Рё устанавливаем разумные размеры
        screen = QApplication.primaryScreen().geometry()
        window_width = min(1400, screen.width() - 100)
        window_height = min(900, screen.height() - 100)
        
        self.resize(window_width, window_height)
        
        # Центрируем окно
        x = (screen.width() - window_width) // 2
        y = (screen.height() - window_height) // 2
        self.move(x, y)

        self.central_widget = QTabWidget()
        self.setCentralWidget(self.central_widget)
        self.central_widget.setUsesScrollButtons(True)  # Важно!
        self.central_widget.tabBar().setExpanding(True)
        # Стилизация таб-бара
        self.central_widget.setStyleSheet(AppStyles.tab_widget())
        self.central_widget.setCurrentIndex(0)
        self.central_widget.tabBar().setVisible(True)
        # Первая вкладка - список документов
        self.setup_documents_tab()
        
        # Вторая вкладка - редактор метаданных
        self.metadata_editor = MetadataEditor(self.db_manager)
        self.central_widget.addTab(self.metadata_editor, "Редактор метаданных")
        
        # Третья вкладка - просмотр документа (скрытая)
        #self.setup_preview_tab()
        self.central_widget.setCurrentIndex(0)  
        # Скрываем вкладку предпросмотра
        
        # Боковые панели (перенесено после определения всех методов)
        self.setup_dock_widgets()
        # Верхнее меню
        self.setup_menu()
        
        # Сигналы Рё слоты
        self.setup_connections()
        self.central_widget.currentChanged.connect(self.on_main_tab_changed)
        self.setup_background_indicator()
        
        
        self.show()

    def setup_background_indicator(self):
        """Create one shared status indicator for long operations."""
        self.background_ops_widget = QWidget(self)
        layout = QHBoxLayout(self.background_ops_widget)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(8)

        self.background_ops_label = QLabel("Фоновая операция...")
        self.background_ops_label.setStyleSheet("color: #2c3e50; font-size: 9pt;")

        self.background_ops_progress = QProgressBar()
        self.background_ops_progress.setRange(0, 0)
        self.background_ops_progress.setFixedWidth(120)
        self.background_ops_progress.setFixedHeight(12)

        layout.addWidget(self.background_ops_label)
        layout.addWidget(self.background_ops_progress)
        self.background_ops_widget.hide()

        self.statusBar().addPermanentWidget(self.background_ops_widget)

    def begin_background_operation(self, operation_key: str, message: str):
        self._background_operations[operation_key] = message
        self._refresh_background_indicator()

    def update_background_operation(self, operation_key: str, message: str = None):
        if operation_key not in self._background_operations:
            return
        if message:
            self._background_operations[operation_key] = message
        self._refresh_background_indicator()
        QApplication.processEvents()

    def end_background_operation(self, operation_key: str):
        self._background_operations.pop(operation_key, None)
        self._refresh_background_indicator()

    def _refresh_background_indicator(self):
        if not hasattr(self, "background_ops_widget"):
            return

        if not self._background_operations:
            self.background_ops_widget.hide()
            return

        active_message = list(self._background_operations.values())[-1]
        self.background_ops_label.setText(active_message)
        self.background_ops_widget.show()

    def _settings_set_json(self, key: str, value):
        self.settings.setValue(key, json.dumps(value, ensure_ascii=False))

    def _settings_get_json(self, key: str, default):
        raw_value = self.settings.value(key, "")
        if not raw_value:
            return default
        try:
            return json.loads(raw_value)
        except Exception:
            return default

    def _refresh_saved_searches_combo(self):
        if not hasattr(self, "saved_searches_combo"):
            return

        self.saved_searches_combo.blockSignals(True)
        self.saved_searches_combo.clear()
        self.saved_searches_combo.addItem("💾 Сохраненные поиски", None)
        for preset_name in sorted(self.saved_searches.keys()):
            self.saved_searches_combo.addItem(preset_name, preset_name)
        self.saved_searches_combo.blockSignals(False)

    def _capture_current_search_preset(self):
        return {
            "filters": self.get_current_filters(),
            "quick_search_text": self.quick_search_box.text().strip() if hasattr(self, "quick_search_box") else "",
            "mode_index": self.mode_selector.currentIndex() if hasattr(self, "mode_selector") else 0,
        }

    def save_current_search_preset(self):
        preset_name, ok = QInputDialog.getText(
            self,
            "Сохранить поиск",
            "Название пресета:"
        )
        preset_name = preset_name.strip()

        if not ok or not preset_name:
            return

        if preset_name in self.saved_searches:
            overwrite = QMessageBox.question(
                self,
                "Перезаписать пресет",
                f"Пресет '{preset_name}' уже существует. Перезаписать?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if overwrite != QMessageBox.Yes:
                return

        self.saved_searches[preset_name] = self._capture_current_search_preset()
        self._settings_set_json("saved_searches", self.saved_searches)
        self._refresh_saved_searches_combo()

        index = self.saved_searches_combo.findData(preset_name)
        if index >= 0:
            self.saved_searches_combo.setCurrentIndex(index)

        self.statusBar().showMessage(f"Пресет '{preset_name}' сохранен", 3000)

    def apply_selected_saved_search(self, _index=None):
        if not hasattr(self, "saved_searches_combo"):
            return
        preset_name = self.saved_searches_combo.currentData()
        if not preset_name:
            return
        self.apply_saved_search(preset_name)

    def apply_saved_search(self, preset_name: str):
        preset = self.saved_searches.get(preset_name)
        if not preset:
            return

        mode_index = preset.get("mode_index", 0)
        try:
            mode_index = int(mode_index)
        except Exception:
            mode_index = 0

        if hasattr(self, "mode_selector") and mode_index in (0, 1):
            self.mode_selector.setCurrentIndex(mode_index)

        filters = preset.get("filters", {})
        if not isinstance(filters, dict):
            filters = {}

        if filters and any(filters.values()):
            self.apply_filters(filters)
        else:
            self.documents_table_view.load_documents()
            if self.current_compact_mode == 1:
                self.load_quick_access_documents()
            else:
                self.compact_documents_table.load_documents()
                self.update_compact_table_count()

        quick_search_text = str(preset.get("quick_search_text", "")).strip()
        if hasattr(self, "quick_search_box"):
            self.quick_search_box.setText(quick_search_text)
            if quick_search_text:
                self.filter_documents_list(quick_search_text)

        self.statusBar().showMessage(f"Пресет '{preset_name}' применен", 3000)

    def delete_selected_saved_search(self):
        if not hasattr(self, "saved_searches_combo"):
            return

        preset_name = self.saved_searches_combo.currentData()
        if not preset_name:
            QMessageBox.information(self, "Удаление пресета", "Выберите пресет для удаления")
            return

        reply = QMessageBox.question(
            self,
            "Удаление пресета",
            f"Удалить пресет '{preset_name}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self.saved_searches.pop(preset_name, None)
        self._settings_set_json("saved_searches", self.saved_searches)
        self._refresh_saved_searches_combo()
        self.statusBar().showMessage(f"Пресет '{preset_name}' удален", 3000)

    def save_ui_state(self):
        try:
            ui_state = {
                "window_geometry": bytes(self.saveGeometry().toBase64()).decode("ascii"),
                "window_state": bytes(self.saveState().toBase64()).decode("ascii"),
                "left_dock_visible": self.left_dock.isVisible() if hasattr(self, "left_dock") else True,
                "right_dock_visible": self.right_dock.isVisible() if hasattr(self, "right_dock") else True,
                "left_dock_width": self.left_dock.width() if hasattr(self, "left_dock") else 500,
                "right_dock_width": self.right_dock.width() if hasattr(self, "right_dock") else 250,
                "quick_search_text": self.quick_search_box.text() if hasattr(self, "quick_search_box") else "",
                "compact_mode": self.mode_selector.currentIndex() if hasattr(self, "mode_selector") else 0,
                "quick_access_documents": list(self.quick_access_documents),
                "tools_panel_collapsed": bool(getattr(self, "tools_panel_collapsed", False)),
                "main_tab_index": self.central_widget.currentIndex() if hasattr(self, "central_widget") else 0,
                "filters": self.get_current_filters(),
                "selected_db": getattr(self.switcher, "current_db", None) if self.switcher else None,
            }
            self._settings_set_json("ui_state", ui_state)
            self._settings_set_json("saved_searches", self.saved_searches)
            self.settings.sync()
        except Exception as e:
            print(f"⚠️ Не удалось сохранить состояние UI: {e}")

    def restore_ui_state(self):
        self.saved_searches = self._settings_get_json("saved_searches", {})
        if not isinstance(self.saved_searches, dict):
            self.saved_searches = {}
        self._refresh_saved_searches_combo()

        ui_state = self._settings_get_json("ui_state", {})
        if not isinstance(ui_state, dict) or not ui_state:
            return

        try:
            geometry = ui_state.get("window_geometry")
            if geometry:
                self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        except Exception:
            pass

        try:
            window_state = ui_state.get("window_state")
            if window_state:
                self.restoreState(QByteArray.fromBase64(window_state.encode("ascii")))
        except Exception:
            pass

        quick_access_ids = []
        for value in ui_state.get("quick_access_documents", []):
            try:
                quick_access_ids.append(int(value))
            except Exception:
                continue
        self.quick_access_documents = quick_access_ids

        saved_db_name = ui_state.get("selected_db")
        if self.switcher and saved_db_name and saved_db_name != getattr(self.switcher, "current_db", None):
            if saved_db_name in getattr(self.switcher, "databases", {}):
                self.begin_background_operation("db_restore", f"Восстановление БД: {saved_db_name}")
                try:
                    restored_manager = self.switcher.switch_database(saved_db_name)
                    self.db_manager = restored_manager
                    self.documents_table_view.db_manager = restored_manager
                    self.compact_documents_table.db_manager = restored_manager
                    self.metadata_editor.db_manager = restored_manager
                    self.metadata_widget.db_manager = restored_manager
                    if hasattr(self, "tag_search_widget"):
                        self.tag_search_widget.db_manager = restored_manager
                        self.tag_search_widget.clear_reference_cache()
                except Exception as e:
                    print(f"⚠️ Не удалось восстановить выбранную БД '{saved_db_name}': {e}")
                finally:
                    self.end_background_operation("db_restore")

        if hasattr(self, "left_dock"):
            self.left_dock.setVisible(bool(ui_state.get("left_dock_visible", True)))
        if hasattr(self, "right_dock"):
            self.right_dock.setVisible(bool(ui_state.get("right_dock_visible", True)))

        left_width = int(ui_state.get("left_dock_width", 500) or 500)
        right_width = int(ui_state.get("right_dock_width", 250) or 250)
        if hasattr(self, "left_dock") and hasattr(self, "right_dock"):
            self.resizeDocks([self.left_dock, self.right_dock], [left_width, right_width], Qt.Horizontal)

        desired_collapsed = bool(ui_state.get("tools_panel_collapsed", False))
        if getattr(self, "tools_panel_collapsed", False) != desired_collapsed:
            self.toggle_tools_panel()

        saved_mode = int(ui_state.get("compact_mode", 0) or 0)
        if hasattr(self, "mode_selector") and saved_mode in (0, 1):
            self.mode_selector.setCurrentIndex(saved_mode)

        saved_filters = ui_state.get("filters", {})
        if isinstance(saved_filters, dict) and saved_filters and any(saved_filters.values()):
            self.apply_filters(saved_filters)
        else:
            self.documents_table_view.load_documents()
            if self.current_compact_mode == 1:
                self.load_quick_access_documents()
            else:
                self.compact_documents_table.load_documents()
                self.update_compact_table_count()

        quick_search_text = str(ui_state.get("quick_search_text", "")).strip()
        if hasattr(self, "quick_search_box"):
            self.quick_search_box.setText(quick_search_text)
            if quick_search_text:
                self.filter_documents_list(quick_search_text)

        saved_tab_index = int(ui_state.get("main_tab_index", 0) or 0)
        if hasattr(self, "central_widget") and 0 <= saved_tab_index < self.central_widget.count():
            self.central_widget.setCurrentIndex(saved_tab_index)

    def closeEvent(self, event):
        self.save_ui_state()
        super().closeEvent(event)

    def show_diagnostics_dialog(self):
        if self.diagnostics_dialog is None:
            self.diagnostics_dialog = DiagnosticsDialog(self)
        self.diagnostics_dialog.set_context(self.db_manager, self.switcher)
        self.diagnostics_dialog.show()
        self.diagnostics_dialog.raise_()
        self.diagnostics_dialog.activateWindow()

    def run_diagnostics_check(self):
        try:
            result = run_diagnostics_checks(self.db_manager, self.switcher)
            if result.get("ok"):
                self.statusBar().showMessage("Диагностика: подключение к базе и файлам работает", 4000)
            else:
                self.statusBar().showMessage("Диагностика обнаружила проблемы, откройте окно 'Внимание'", 6000)
        except Exception as exc:
            self.logger.exception("Ошибка запуска диагностики: %s", exc)

    def _has_unsaved_changes(self, editor):
        if editor is None:
            return False

        for attr_name in ("save_button", "save_btn"):
            button = getattr(editor, attr_name, None)
            if button is not None:
                try:
                    return bool(button.isEnabled())
                except Exception:
                    return False
        return False

    def _handle_remote_document_missing(self, document_id):
        if document_id is None:
            return

        if self.current_document_id == document_id:
            self.current_document_id = None

        if hasattr(self, "metadata_widget") and getattr(self.metadata_widget, "current_document_id", None) == document_id:
            self.metadata_widget.clear_form()
        if hasattr(self, "metadata_editor") and getattr(self.metadata_editor, "current_document_id", None) == document_id:
            self.metadata_editor.clear_form()

        if hasattr(self, "documents_table_view"):
            self.documents_table_view.clearSelection()
        if hasattr(self, "compact_documents_table"):
            self.compact_documents_table.clearSelection()

        if not self._live_sync_in_progress:
            self.sync_document_lists()

        if self._last_remote_missing_document_id != document_id:
            message = (
                f"Документ ID {document_id} был удален или больше недоступен в другой сессии. "
                "Список обновлен."
            )
            self.statusBar().showMessage(message, 7000)
            self.logger.warning(message)
            self._last_remote_missing_document_id = document_id

    def _refresh_open_document_if_needed(self, document_id):
        if not document_id:
            return

        try:
            document_data = self.db_manager.get_document_by_id(document_id)
        except Exception as exc:
            self.logger.warning("Не удалось обновить документ ID %s: %s", document_id, exc)
            return

        if not document_data:
            self._handle_remote_document_missing(document_id)
            return

        self._last_remote_missing_document_id = None

        if (
            hasattr(self, "metadata_widget")
            and getattr(self.metadata_widget, "current_document_id", None) == document_id
            and not self._has_unsaved_changes(self.metadata_widget)
        ):
            self.metadata_widget.set_document(document_data)

        if (
            hasattr(self, "metadata_editor")
            and getattr(self.metadata_editor, "current_document_id", None) == document_id
            and not self._has_unsaved_changes(self.metadata_editor)
        ):
            self.metadata_editor.set_initial_data(document_data)

    def run_live_sync(self):
        if self._live_sync_in_progress or not self.isVisible():
            return

        modal_widget = QApplication.activeModalWidget()
        if modal_widget and modal_widget is not self.diagnostics_dialog:
            return

        self._live_sync_in_progress = True
        try:
            tracked_document_id = self.current_document_id
            self.sync_document_lists()
            if tracked_document_id:
                self._refresh_open_document_if_needed(tracked_document_id)
        except Exception as exc:
            self.logger.warning("Ошибка фоновой синхронизации: %s", exc)
        finally:
            self._live_sync_in_progress = False

    def _toggle_diagnostics_alert(self):
        self.diagnostics_alert_visible = not self.diagnostics_alert_visible
        self._apply_diagnostics_button_style()

    def _on_diagnostics_status_changed(self, _snapshot):
        self._apply_diagnostics_button_style()

    def _apply_diagnostics_button_style(self):
        if not hasattr(self, "diagnostics_btn"):
            return

        snapshot = self.diagnostics_manager.get_status_snapshot()
        health = snapshot.get("health", {})
        is_alert = bool(snapshot.get("alert_active"))
        is_ok = bool(health.get("ok", True))

        if is_alert:
            if not self.diagnostics_blink_timer.isActive():
                self.diagnostics_blink_timer.start()
            base_color = "#ff4d4f" if self.diagnostics_alert_visible else "#8b1e1e"
            hover_color = "#ff7875" if self.diagnostics_alert_visible else "#a61d24"
            text = "Внимание"
        else:
            if self.diagnostics_blink_timer.isActive():
                self.diagnostics_blink_timer.stop()
            self.diagnostics_alert_visible = True
            base_color = "#28a745" if is_ok else "#ff9800"
            hover_color = "#218838" if is_ok else "#fb8c00"
            text = "Внимание"

        self.diagnostics_btn.setText(text)
        self.diagnostics_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {base_color};
                color: white;
                border: none;
                padding: 10px 8px;
                font-size: 9pt;
                font-weight: bold;
                border-radius: 6px;
                text-align: center;
                min-height: 20px;
            }}
            QPushButton:hover {{
                background-color: {hover_color};
            }}
            """
        )

    def open_file_by_document_id(self, doc_id):
        
        """
        Открыть файл документа по его ID в программе по умолчанию
        
        :param doc_id: ID документа в базе данных
        """
        try:
            # Получаем данные документа
            document_data = self.db_manager.get_document_by_id(doc_id)
            if not document_data:
                self._handle_remote_document_missing(doc_id)
                return
            
            if not document_data:
                QMessageBox.warning(
                    self,
                    "⚠️ Документ не найден",
                    f"Документ с ID {doc_id} не найден в базе данных"
                )
                return
            
            # Получаем путь к файлу
            relative_path = document_data.get("document_path")
            
            if not relative_path:
                QMessageBox.warning(
                    self,
                    "⚠️ Путь не указан",
                    f"У документа '{document_data.get('title', 'Без названия')}' не указан путь к файлу"
                )
                return
            
            # Преобразуем в полный путь
            full_path = self.db_manager.get_full_file_path(relative_path)
            
            # Открываем файл
            self.open_file_in_default_app(full_path)
            
        except Exception as e:
            QMessageBox.critical(
                self,
                "❌ Ошибка",
                f"Не удалось открыть файл:\n\n{str(e)}"
            )
            print(f"❌ Ошибка в open_file_by_document_id: {e}")
    def resizeEvent(self, event):
        """Обработка изменения размера окна"""
        super().resizeEvent(event)
        
        # Проверяем, помещаются ли вкладки
        tab_bar = self.central_widget.tabBar()
        if tab_bar.width() > self.width() - 50:
            # Включаем скроллинг если вкладки не помещаются
            self.central_widget.setUsesScrollButtons(True) 
    def apply_filters(self, filters):
        """Применение фильтров к списку документов"""
        try:
            # Обновляем полную таблицу
            if hasattr(self, 'documents_table_view'):
                self.documents_table_view.load_documents(filters)
                
            # Обновляем компактный список (если есть)
            if hasattr(self, 'compact_documents_table'):
                # Загрузить те же документы
                self.compact_documents_table.load_documents(filters)
                self.update_compact_table_count()
            
        except Exception as e:
            print(f"❌ Ошибка применения фильтров: {e}")

    def get_filtered_documents(self, filters):
        """Получить отфильтрованные документы из БД"""
        try:
            return self.db_manager.search_documents_with_filters(filters or {})
            
        except Exception as e:
            print(f"❌ Ошибка получения документов: {e}")
            return []
    def open_document_preview_by_id(self, doc_id):
        """
        Open quick preview for a document by ID.
        
        Changes:
        1. Uses QuickPreviewDialog instead of the old preview dialog.
        2. Resolves file path through db_manager.
        3. Improves error handling.
        """
        try:
            # Проверяем, не открыт ли уже диалог
            if hasattr(self, '_quick_preview_dialog') and self._quick_preview_dialog.isVisible():
                self._quick_preview_dialog.raise_()
                self._quick_preview_dialog.activateWindow()
                return
            
            # Получаем данные документа
            document_data = self.db_manager.get_document_by_id(doc_id)
            
            if not document_data:
                QMessageBox.warning(
                    self, 
                    "Ошибка", 
                    f"Документ с ID {doc_id} не найден"
                )
                return
            
            # Корректно берем относительный путь документа
            relative_path = document_data.get("document_path")
            
            if not relative_path:
                QMessageBox.warning(
                    self, 
                    "Ошибка", 
                    f"У документа не указан путь к файлу"
                )
                return
            
            # Используем backend-метод для получения полного пути
            import os
            full_path = self.db_manager.get_full_file_path(relative_path)
            
            # Проверяем существование файла
            if not os.path.exists(full_path):
                QMessageBox.critical(
                    self, 
                    "Файл не найден", 
                    f"Файл документа не найден:\n\n"
                    f"📁 Путь: {full_path}\n\n"
                    f"Возможные причины:\n"
                    f"• Файл был удалён или перемещён\n"
                    f"• Неверный путь в базе данных"
                )
                print(f"❌ Файл не найден: {full_path}")
                return
            
            # Открываем быстрый предпросмотр
            print(f"⚡ Открываем быстрый предпросмотр: {full_path}")
            
            self._quick_preview_dialog = QuickPreviewDialog(
                self.document_handler, 
                full_path, 
                self
            )
            self._quick_preview_dialog.finished.connect(self.on_quick_preview_closed)
            self._quick_preview_dialog.show()
            
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"❌ Ошибка при открытии предпросмотра:\n{error_details}")
            
            QMessageBox.critical(
                self,
                "Ошибка",
                f"Не удалось открыть предпросмотр:\n\n{str(e)}"
            )

    def show_advanced_search(self):
        """Показать диалог расширенного поиска с результатами"""
        try:
            should_create_dialog = (
                not hasattr(self, "search_dialog")
                or self.search_dialog is None
                or getattr(self.search_dialog, "db_manager", None) is not self.db_manager
            )

            if should_create_dialog:
                self.search_dialog = AdvancedSearchDialog(self.db_manager, self.document_handler, self)
                self.search_dialog.edit_metadata_requested.connect(self.open_metadata_editor_tab)
                self.search_dialog.preview_requested.connect(self.open_document_preview_by_id)

            self.search_dialog.exec_()
        except Exception as e:
            print(f"Ошибка открытия диалога поиска: {e}")
    

    def perform_advanced_search(self, criteria):
        """Выполнить расширенный поиск"""
        try:
            documents = self.db_manager.search_by_tags(**criteria)
            self.documents_table_view.update_documents(documents)
            self.central_widget.setCurrentIndex(0)  # Переключиться на вкладку документов
        except Exception as e:
            print(f"Ошибка выполнения поиска: {e}")

    def show_document_preview(self, filename):
        """Показать диалог предпросмотра документа"""
        try:
            self.preview_dialog = self.open_document_preview_by_id(self.document_handler, filename, self)
            self.preview_dialog.exec_()
        except Exception as e:
            print(f"Ошибка открытия предпросмотра: {e}")
    def setup_documents_tab(self):
        """Настройка вкладки со списком документов"""
        self.documents_tab = QWidget()
        self.documents_tab_layout = QVBoxLayout()

        # Таблица документов
        self.documents_table_view = DocumentsTableView(self.db_manager, self.document_handler)
        
        # 🆕 НОВОЕ: Панель тегового поиска
        
        self.tag_search_widget = SimpleTagSearchWidget(self.db_manager)
        self.tag_search_widget.search_requested.connect(self.on_tag_search)
        self.tag_search_widget.tags_cleared.connect(self.on_tags_cleared)
        self.documents_tab_layout.addWidget(self.tag_search_widget)
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        separator.setStyleSheet(f"""
            QFrame {{
                color: {AppColors.GRAY_300};
                background-color: {AppColors.GRAY_300};
                border: none;
                height: 2px;
                margin: 10px 0px;
            }}
        """)
        self.documents_tab_layout.addWidget(separator)
        # Таблица
        self.documents_tab_layout.addWidget(self.documents_table_view)
        
        # 🆕 НОВОЕ: Панель индикатора загрузки
        pagination_panel = QWidget()
        pagination_layout = QHBoxLayout()
        pagination_layout.setContentsMargins(10, 5, 10, 5)
        
        # РРЅРґРёРєР°С‚РѕСЂ загрузки
        self.pagination_info_label = QLabel("📊 Загружено: 0 / 0")
        self.pagination_info_label.setStyleSheet("""
            QLabel {
                font-size: 10pt;
                color: #6c757d;
                font-weight: 500;
                padding: 5px 10px;
                background-color: #f8f9fa;
                border-radius: 4px;
            }
        """)
        
        
        
        pagination_layout.addWidget(self.pagination_info_label)
        pagination_layout.addStretch()
        
        pagination_panel.setLayout(pagination_layout)
        
        self.documents_tab_layout.addWidget(pagination_panel)
        
        self.documents_tab.setLayout(self.documents_tab_layout)
        self.central_widget.addTab(self.documents_tab, "Список документов")
        
        # Установи стиль для табов
        self.central_widget.setStyleSheet("""
            QTabBar::tab {
                font-size: 18pt;
                font-weight: 500;
                padding: 10px 20px;
                min-width: 500px;
            }
        """)
    def update_pagination_info(self, loaded, total, has_more):
        """
        Обновить индикатор пагинации
        
        Args:
            loaded: Количество загруженных документов
            total: Общее количество документов
            has_more: Есть ли еще документы для загрузки
        """
        try:
            # Форматируем числа с разделителями тысяч
            loaded_str = f"{loaded:,}".replace(",", " ")
            total_str = f"{total:,}".replace(",", " ")
            
            # Обновляем текст
            self.pagination_info_label.setText(f"📊 Загружено: {loaded_str} / {total_str}")
            
            # Меняем цвет в зависимости от состояния
            if has_more:
                # Есть еще документы - синий цвет
                color = "#17a2b8"
            elif total == 0:
                # Ничего не найдено - серый
                color = "#6c757d"
            else:
                # Все загружено - зеленый
                color = "#28a745"
            
            self.pagination_info_label.setStyleSheet(f"""
                QLabel {{
                    font-size: 10pt;
                    color: {color};
                    font-weight: 500;
                    padding: 5px 10px;
                    background-color: #f8f9fa;
                    border-radius: 4px;
                }}
            """)
            
        except Exception as e:
            print(f"❌ Ошибка обновления индикатора: {e}")
    def on_tags_cleared(self):
        """
        Обработчик очистки всех тегов
        
        Вызывается когда удаляется последний тег или нажимается кнопка "Очистить все"
        Восстанавливает таблицу к исходному состоянию (последние 100 документов)
        """
        try:
            print("🧹 Теги очищены - загружаем последние документы")
            
            # Загружаем последние 100 документов в основную таблицу
            self.documents_table_view.load_documents()
            
            # Обновляем компактный список если есть
            if hasattr(self, 'compact_documents_table'):
                self.compact_documents_table.load_documents()
                self.update_compact_table_count()
            
            print("✅ Таблица восстановлена к исходному состоянию")
            
        except Exception as e:
            print(f"❌ Ошибка восстановления таблицы: {e}")
            import traceback
            traceback.print_exc()
    def on_tag_search(self, search_data):
        """Обработчик поиска по тегам"""
        # 1. Выполняет SQL-запрос через execute_simple_tag_search
        filters = {}
        if isinstance(search_data, dict):
            filters = search_data.get("filters", search_data)
        if not isinstance(filters, dict):
            filters = {}
        
        # 2. Обновляет основную таблицу
        self.apply_filters(filters)
        self.central_widget.setCurrentIndex(0)
        
        # 3. Обновляет компактный список в боковой панели
        #self.compact_documents_table.update_data(compact_data)

    def sync_tabs_with_sidebar(self, index):
        """Синхронизация главных вкладок с боковой панелью"""
        # Защита от рекурсивных вызовов
        if hasattr(self, '_syncing_tabs') and self._syncing_tabs:
            return
            
        self._syncing_tabs = True
        
        try:
            if index == 0:  # Вкладка "Список документов"
                if self.mode_selector.currentIndex() != 0:
                    self.mode_selector.setCurrentIndex(0)
                self.left_dock.setWindowTitle("📋 Метаданные документа")
            elif index == 2:  # Вкладка "Редактор метаданных"
                if self.mode_selector.currentIndex() != 1:
                    self.mode_selector.setCurrentIndex(1)
                self.left_dock.setWindowTitle("📄 Список документов")
                # Обновляем список при переключении
                self.refresh_documents_list()
        finally:
            self._syncing_tabs = False
    def on_main_tab_changed(self, index):
        """Обработчик смены вкладки в основном окне"""
        # 0 - Список документов, 1 - Редактор метаданных, 2 - Просмотр
        if index == 0:  # Вкладка списка документов
            
            self.left_dock.setWidget(self.metadata_widget)
            
        elif index == 1:  # Вкладка редактора метаданных
            
            self.left_dock.setWidget(self.documents_list_widget)
            # Обновляем список при переключении
            
            
        elif index == 2:  # Вкладка предпросмотра
            self.left_dock.setWindowTitle("📋 Метаданные документа")
            self.left_dock.setWidget(self.metadata_widget)

    
    def load_document_to_main_editor(self, doc_id):
        """Загружает метаданные документа в основной редактор"""
        try:
            document_data = self.db_manager.get_document_by_id(doc_id)
            if document_data:
                self.metadata_editor.set_initial_data(document_data)
        except Exception as e:
            print(f"❌ Ошибка загрузки в основной редактор: {e}")

    def setup_menu(self):
        """Настройка меню (сохраняем твою структуру)"""
        menubar = self.menuBar()
        file_menu = menubar.addMenu("Файл")
        edit_menu = menubar.addMenu("Правка")
        view_menu = menubar.addMenu("Вид")
        help_menu = menubar.addMenu("Помощь")
        
        # Добавляем пункт меню "Добавить документ"
        add_action = QAction("Добавить документ...", self)
        add_action.triggered.connect(self.show_add_document_dialog)
        file_menu.addAction(add_action)

        # Добавляем действия для управления видимостью док-виджетов
        toggle_left_dock = QAction("Левая панель", self)
        toggle_left_dock.setCheckable(True)
        toggle_left_dock.setChecked(True)
        toggle_left_dock.triggered.connect(self.left_dock.setVisible)
        toggle_right_dock = QAction("Правая панель", self)
        toggle_right_dock.setCheckable(True)
        toggle_right_dock.setChecked(True)
        toggle_right_dock.triggered.connect(self.right_dock.setVisible)
        
        view_menu.addAction(toggle_left_dock)
        view_menu.addAction(toggle_right_dock)
    def show_database_switcher(self):
        """Показать диалог переключения БД"""
        dialog = DatabaseSwitcherDialog(self)
        dialog.database_changed.connect(self.on_database_switched)
        
        # РР—РњР•РќР•РќРР•: Получаем результат диалога
        dialog.exec_()


    def on_database_switched(self, db_name, new_manager):
        """Обработка переключения БД с улучшенной обработкой ошибок"""
        print(f"\n{'='*60}")
        print(f"🔄 НАЧАЛО ПЕРЕКЛЮЧЕНИЯ БД: {db_name}")
        print(f"{'='*60}\n")
        
        reply = QMessageBox.question(
            self,
            "Перезагрузка данных",
            "Перезагрузить данные из новой базы?",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self.db_manager = new_manager
            if self.diagnostics_dialog is not None:
                self.diagnostics_dialog.set_context(self.db_manager, self.switcher)
            # Проверяем что db_manager валиден
            if not self.db_manager or not self.db_manager.conn:
                QMessageBox.critical(
                    self,
                    "Ошибка",
                    "Не удалось получить новое подключение к БД"
                )
                return
            
            # Показываем прогресс
            progress = QProgressDialog(
                "Загрузка данных из новой базы...",
                "Отмена",
                0, 100,
                self
            )
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.show()
            self.begin_background_operation("db_switch", f"Переключение БД: {db_name}")
            
            try:
                # ========================================
                # Шаг 1: Очищаем таблицы (10%)
                # ========================================
                print("📊 Шаг 1/6: Очистка таблиц...")
                progress.setValue(10)
                progress.setLabelText("Очистка таблиц...")
                self.update_background_operation("db_switch", "Переключение БД: очистка таблиц...")
                QApplication.processEvents()
                
                try:
                    if hasattr(self, 'documents_table_view') and hasattr(self.documents_table_view, 'model_instance'):
                        self.documents_table_view.model_instance.update_data([])
                    if hasattr(self, 'compact_documents_table') and hasattr(self.compact_documents_table, 'model_instance'):
                        self.compact_documents_table.model_instance.update_data([])
                    print("✅ Таблицы очищены")
                except Exception as e:
                    print(f"⚠️ Ошибка очистки таблиц: {e}")
                
                # ========================================
                # Шаг 2: Обновляем ссылки на db_manager (25%)
                # ========================================
                print("📊 Шаг 2/6: Обновление ссылок...")
                progress.setValue(25)
                progress.setLabelText("Обновление ссылок на БД...")
                self.update_background_operation("db_switch", "Переключение БД: обновление ссылок...")
                QApplication.processEvents()
                
                try:
                    if hasattr(self, 'documents_table_view'):
                        self.documents_table_view.db_manager = self.db_manager
                    if hasattr(self, 'compact_documents_table'):
                        self.compact_documents_table.db_manager = self.db_manager
                    if hasattr(self, 'metadata_editor'):
                        self.metadata_editor.db_manager = self.db_manager
                    if hasattr(self, 'metadata_widget'):
                        self.metadata_widget.db_manager = self.db_manager
                    if hasattr(self, 'search_dialog') and self.search_dialog is not None:
                        self.search_dialog.close()
                        self.search_dialog = None
                    print("✅ Ссылки обновлены")
                except Exception as e:
                    print(f"⚠️ Ошибка обновления ссылок: {e}")
                
                # ========================================
                # Шаг 3: Очищаем формы (40%)
                # ========================================
                print("📊 Шаг 3/6: Очистка форм...")
                progress.setValue(40)
                progress.setLabelText("Очистка форм...")
                QApplication.processEvents()
                
                # Полный редактор метаданных
                try:
                    if hasattr(self, 'metadata_editor'):
                        print("  🧹 Очистка полного редактора...")
                        self.metadata_editor.current_document_id = None
                        
                        if hasattr(self.metadata_editor, 'current_document_display'):
                            self.metadata_editor.current_document_display.setText("📄 Документ не выбран")
                        
                        # Очищаем списки подписантов Рё согласующих
                        if hasattr(self.metadata_editor, 'signers_list'):
                            self.metadata_editor.signers_list.clear()
                        if hasattr(self.metadata_editor, 'approvers_list'):
                            self.metadata_editor.approvers_list.clear()
                        
                        # Критично: очищаем кешированные данные
                        self.metadata_editor.signers_data = []
                        self.metadata_editor.approvers_data = []
                        
                        print("  ✅ Полный редактор очищен")
                except Exception as e:
                    print(f"  ⚠️ Ошибка очистки полного редактора: {e}")
                
                # Компактный редактор
                try:
                    if hasattr(self, 'metadata_widget') and hasattr(self.metadata_widget, 'clear_form'):
                        print("  🧹 Очистка компактного редактора...")
                        self.metadata_widget.clear_form()
                        print("  ✅ Компактный редактор очищен")
                except Exception as e:
                    print(f"  ⚠️ Ошибка очистки компактного редактора: {e}")
                
                # ========================================
                # Шаг 4: Создаем новый ReferenceManager (55%)
                # ========================================
                print("📊 Шаг 4/6: Создание нового ReferenceManager...")
                progress.setValue(55)
                progress.setLabelText("Создание менеджера справочников...")
                QApplication.processEvents()

                try:
                    from metadata_form import ReferenceManager
                    
                    if hasattr(self, 'metadata_editor'):
                        print("  🔧 Создание нового ReferenceManager для полного редактора...")
                        
                        # Создаем новый менеджер
                        new_ref_manager = ReferenceManager(self.db_manager)
                        self.metadata_editor.reference_manager = new_ref_manager
                        
                        # Критично: обновляем ссылки прямо в комбобоксах
                        print("  🔄 Обновление ссылок reference_manager в комбобоксах...")
                        
                        combobox_fields = [
                            'status_field',
                            'type_field',
                            'document_kind_field',
                            'signing_type_field',
                            'executor_field',              # ← ВАЖНО!
                            'responsible_executor_field',  # ← ВАЖНО!
                            'theme_field',
                            'published_where_field'        # ← ВАЖНО! Главное проблемное поле
                        ]
                        
                        for field_name in combobox_fields:
                            if hasattr(self.metadata_editor, field_name):
                                field = getattr(self.metadata_editor, field_name)
                                
                                # Проверяем что это EditableComboBox с reference_manager
                                if hasattr(field, 'reference_manager'):
                                    print(f"    ↻ Обновление ссылки в {field_name}...")
                                    # ⭐ ОБНОВЛЯЕМ ССЫЛКУ НА НОВЫЙ МЕНЕДЖЕР
                                    field.reference_manager = new_ref_manager
                        
                        print("  ✅ ReferenceManager создан Рё ссылки обновлены")
                        
                except Exception as e:
                    print(f"  ⚠️ Ошибка создания ReferenceManager: {e}")
                    import traceback
                    traceback.print_exc()

                # ========================================
                # Шаг 5: Перезагружаем справочники (70%)
                # ========================================
                print("📊 Шаг 5/6: Перезагрузка справочников...")
                progress.setValue(70)
                progress.setLabelText("Загрузка справочников...")
                QApplication.processEvents()

                # Полный редактор - перезагружаем комбобоксы
                try:
                    if hasattr(self, 'metadata_editor'):
                        print("  📋 Загрузка справочников в полном редакторе...")
                        
                        # РСЃРїРѕР»СЊР·СѓРµРј тот же список полей
                        combobox_fields = [
                            'status_field',
                            'type_field',
                            'document_kind_field',
                            'signing_type_field',
                            'executor_field',
                            'responsible_executor_field',
                            'theme_field',
                            'published_where_field'  # ← Главное проблемное поле!
                        ]
                        
                        for field_name in combobox_fields:
                            try:
                                if hasattr(self.metadata_editor, field_name):
                                    field = getattr(self.metadata_editor, field_name)
                                    
                                    if hasattr(field, 'load_items'):
                                        print(f"    ↻ Загрузка данных в {field_name}...")
                                        field.load_items()
                                        QApplication.processEvents()
                                        
                            except Exception as e:
                                print(f"    ⚠️ Ошибка загрузки {field_name}: {e}")
                        
                        # Критично: перезагружаем подписантов и согласующих
                        print("  📋 Загрузка подписантов и согласующих...")
                        if hasattr(self.metadata_editor, 'load_signers_approvers_data'):
                            try:
                                self.metadata_editor.load_signers_approvers_data()
                                print(f"    ✅ Загружено подписантов: {len(self.metadata_editor.signers_data)}")
                                print(f"    ✅ Загружено согласующих: {len(self.metadata_editor.approvers_data)}")
                            except Exception as e:
                                print(f"    ⚠️ Ошибка загрузки подписантов/согласующих: {e}")
                        
                        print("  ✅ Справочники полного редактора загружены")
                        
                except Exception as e:
                    print(f"  ⚠️ Общая ошибка загрузки справочников: {e}")
                    import traceback
                    traceback.print_exc()

                # Компактный редактор
                try:
                    if hasattr(self, 'metadata_widget'):
                        print("  📋 Загрузка справочников в компактном редакторе...")
                        if hasattr(self.metadata_widget, 'load_executors'):
                            self.metadata_widget.load_executors()
                        if hasattr(self.metadata_widget, 'load_themes'):
                            self.metadata_widget.load_themes()
                        if hasattr(self.metadata_widget, 'load_statuses'):
                            self.metadata_widget.load_statuses()
                        if hasattr(self.metadata_widget, 'load_document_types'):
                            self.metadata_widget.load_document_types()
                        if hasattr(self.metadata_widget, 'load_document_kinds'):
                            self.metadata_widget.load_document_kinds()
                        if hasattr(self.metadata_widget, 'load_signing_types'):
                            self.metadata_widget.load_signing_types()
                        print("  ✅ Справочники компактного редактора загружены")
                except Exception as e:
                    print(f"  ⚠️ Ошибка загрузки справочников компактного редактора: {e}")
                try:
                    print(self.tag_search_widget._reference_cache)
                    if hasattr(self, 'tag_search_widget') and self.tag_search_widget:
                        self.tag_search_widget.db_manager = new_manager 
                        self.tag_search_widget.clear_reference_cache()
                        self.tag_search_widget._cancel_tag_creation()
                        print("Кэш поиска по тэгам отчищен")
                        print(self.tag_search_widget._reference_cache)
                except:
                    print("Ошибка отчистки кэша")              
                # ========================================
                # Шаг 6: Загружаем документы (85-100%)
                # ========================================
                print("📊 Шаг 6/6: Загрузка документов...")
                progress.setValue(85)
                progress.setLabelText("Загрузка документов...")
                self.update_background_operation("db_switch", "Переключение БД: загрузка документов...")
                QApplication.processEvents()
                
                doc_count = 0
                try:
                    if hasattr(self, 'documents_table_view'):
                        print("  📄 Загрузка в основную таблицу...")
                        self.documents_table_view.load_documents()
                        if hasattr(self.documents_table_view, 'model_instance'):
                            doc_count = self.documents_table_view.model_instance.rowCount()
                        print(f"  ✅ Загружено {doc_count} документов")
                    
                    progress.setValue(92)
                    QApplication.processEvents()
                    
                    if hasattr(self, 'refresh_documents_list'):
                        print("  📄 Обновление компактного списка...")
                        self.refresh_documents_list()
                        print("  ✅ Компактный список обновлен")
                    
                    progress.setValue(96)
                    QApplication.processEvents()
                    
                    if hasattr(self, 'update_compact_table_count'):
                        self.update_compact_table_count()
                    
                except Exception as e:
                    print(f"  ⚠️ Ошибка загрузки документов: {e}")
                    import traceback
                    traceback.print_exc()
                
                # ========================================
                # Завершение
                # ========================================
                print("📦 Обновление статуса архивации...")
                progress.setValue(98)
                progress.setLabelText("Обновление информации об архивации...")
                QApplication.processEvents()

                try:
                    self.update_backup_status()
                    print("✅ Статус архивации обновлен для новой БД")
                except Exception as e:
                    print(f"⚠️ Ошибка обновления статуса архивации: {e}")
                self.run_diagnostics_check()
                progress.setValue(100)
                progress.close()
                print(f"\n{'='*60}")
                print("DB SWITCH COMPLETED")
                print(f"  Database: {db_name}")
                print(f"  Documents loaded: {doc_count}")
                print(f"{'='*60}\n")
                
                QMessageBox.information(
                    self,
                    "✅ Готово",
                    f"Приложение переключено на БД: {db_name}\n\n"
                    f"📊 Загружено документов: {doc_count}"
                )
                self.save_ui_state()
                
            except Exception as e:
                progress.close()
                error_msg = str(e)
                print(f"\n{'='*60}")
                print(f"❌ ОШИБКА ПЕРЕКЛЮЧЕНИЯ БД")
                print(f"  Сообщение: {error_msg}")
                print(f"{'='*60}\n")
                import traceback
                traceback.print_exc()
                
                QMessageBox.critical(
                    self, 
                    "❌ Ошибка переключения БД", 
                    f"Произошла ошибка:\n\n{error_msg}\n\n"
                    f"Проверьте консоль для подробностей."
                )
            finally:
                self.end_background_operation("db_switch")
    def setup_dock_widgets(self):
        """Настройка боковых панелей."""
        # Левая панель - будет содержать либо метаданные, либо список документов
        self.left_dock = QDockWidget(self)
        self.left_dock.setObjectName("leftDock")
        self.left_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.left_dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.left_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.left_dock.setMinimumWidth(360)
        self.left_dock.setMaximumWidth(900)
        # Создаем оба виджета для левой панели
        self.metadata_widget = CompactMetadataEditor(self.db_manager, self)
        self.documents_list_widget = self.create_documents_list_widget()
        self.left_dock.setStyleSheet(AppStyles.dock_widget())
        # РР·РЅР°С‡Р°Р»СЊРЅРѕ показываем метаданные
        self.left_dock.setWidget(self.metadata_widget)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.left_dock)
        
        # Правая панель - инструменты
        self.right_dock = QDockWidget("🛠 Инструменты", self)
        self.right_dock.setObjectName("rightDock")
        self.right_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.right_dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.setup_collapsible_tools_panel()
        self.right_dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self.right_dock.setMinimumWidth(200)
        self.right_dock.setMaximumWidth(360)
        self.resizeDocks([self.right_dock], [250], Qt.Horizontal)
        self.addDockWidget(Qt.RightDockWidgetArea, self.right_dock)
        self.right_dock.setStyleSheet(AppStyles.dock_widget())
        # Подключаем сигнал сохранения метаданных
        

    def create_documents_list_widget(self):
        """Создать виджет списка документов с реальными данными"""
        widget = QWidget()
        layout = QVBoxLayout()
        
        # Заголовок с переключателем режимов
        header_widget = QWidget()
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)

        # Переключатель режимов
        self.mode_selector = QComboBox()
        self.mode_selector.addItem("📄 Список документов")
        self.mode_selector.addItem("⚡ Быстрый доступ")
        self.mode_selector.setCurrentIndex(0)
        self.mode_selector.setStyleSheet("""
            QComboBox {
                background-color: #3498db;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 10pt;
                font-weight: bold;
                min-width: 180px;
            }
            QComboBox:hover {
                background-color: #2980b9;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid white;
                margin-right: 5px;
            }
            QComboBox QAbstractItemView {
                background-color: white;
                color: #2c3e50;
                selection-background-color: #3498db;
                selection-color: white;
                border: 1px solid #bdc3c7;
            }
        """)
        self.mode_selector.currentIndexChanged.connect(self.on_compact_mode_changed)

        header_layout.addWidget(self.mode_selector)
        header_layout.addStretch()
        header_widget.setLayout(header_layout)
        
        # Поле поиска
        self.quick_search_box = QLineEdit()
        self.quick_search_box.setPlaceholderText("🔍 Поиск документов...")
        self.quick_search_box.setStyleSheet("""
            QLineEdit {
                transition: border-color 0.2s ease;
                padding: 6px;
                border: 1px solid #bdc3c7;
                border-radius: 4px;
                font-size: 9pt;
            }
            QLineEdit:focus {
                border-color: #3498db;
            }
        """)
        self.quick_search_box.textChanged.connect(self.filter_documents_list)

        # Сохраненные поиски (пресеты)
        preset_widget = QWidget()
        preset_layout = QHBoxLayout(preset_widget)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.setSpacing(6)

        self.saved_searches_combo = QComboBox()
        self.saved_searches_combo.setMinimumWidth(180)
        self.saved_searches_combo.currentIndexChanged.connect(self.apply_selected_saved_search)

        self.save_preset_btn = QPushButton("Сохранить")
        self.save_preset_btn.setStyleSheet(AppStyles.button_primary())
        self.save_preset_btn.clicked.connect(self.save_current_search_preset)

        self.delete_preset_btn = QPushButton("Удалить")
        self.delete_preset_btn.setStyleSheet(AppStyles.button_danger())
        self.delete_preset_btn.clicked.connect(self.delete_selected_saved_search)

        preset_layout.addWidget(self.saved_searches_combo, 1)
        preset_layout.addWidget(self.save_preset_btn)
        preset_layout.addWidget(self.delete_preset_btn)
        self._refresh_saved_searches_combo()
        
        # Создаем компактную таблицу документов
        self.compact_documents_table = CompactDocumentsTableView(self.db_manager)
        
        # Подключаем сигналы компактной таблицы
        self.compact_documents_table.show_metadata_tab.connect(self.open_metadata_editor_tab)
        
        # РРЅС„РѕСЂРјР°С†РёРѕРЅРЅР°СЏ панель
        
        
        # Счетчик документов
        self.documents_count_label = QLabel("📊 Документов: 0")
        self.documents_count_label.setStyleSheet("""
            QLabel {
                background-color: #f8f9fa;
                border: 1px solid #dee2e6;
                border-radius: 4px;
                padding: 4px;
                font-size: 8pt;
                color: #6c757d;
            }
        """)
        
        # Сборка виджета
        layout.addWidget(header_widget)
        layout.addWidget(self.quick_search_box)
        layout.addWidget(preset_widget)
        layout.addWidget(self.compact_documents_table, 1)
        layout.addWidget(self.documents_count_label)
        
        
        widget.setLayout(layout)
        return widget

    def refresh_documents_list(self):
        """Обновить список документов в боковой панели"""
        try:
            self.compact_documents_table.load_documents()  # БЕЗ параметров = последние 100
            self.update_compact_table_count()
            print(f"🔄 Обновлен компактный список документов")
        except Exception as e:
            print(f"❌ Ошибка обновления списка документов: {e}")

    def filter_documents_list(self, text):
        """Фильтрация документов в боковой панели."""
        try:
            text = text.strip().lower()

            if not text:
                filters = self.get_current_filters()
                if self.current_compact_mode == 1:
                    self.load_quick_access_documents()
                elif filters and any(filters.values()):
                    self.compact_documents_table.load_documents(filters)
                else:
                    self.compact_documents_table.load_documents()
                self.update_compact_table_count()
                return

            if self.current_compact_mode == 1:
                if not self.quick_access_documents:
                    self.compact_documents_table.update_data([])
                    self.update_compact_table_count()
                    return
                documents = self.db_manager.get_compact_documents(
                    filters={"search_text": text},
                    document_ids=self.quick_access_documents,
                    limit=1000,
                )
            else:
                filters = self.get_current_filters()
                filters["search_text"] = text
                documents = self.db_manager.get_compact_documents(filters=filters, limit=1000)

            table_data = self.compact_documents_table._format_table_data(documents)
            self.compact_documents_table.update_data(table_data)
            self.update_compact_table_count()
            print(f"🔍 Локальный поиск: найдено {len(table_data)} документов")

        except Exception as e:
            print(f"❌ Ошибка локального поиска: {e}")
            import traceback
            traceback.print_exc()

    def on_compact_document_selected(self, doc_id):
        """Handle document selection from the compact list."""
        try:
            self.current_document_id = doc_id
            document_data = self.db_manager.get_document_by_id(doc_id)
            if not document_data:
                self._handle_remote_document_missing(doc_id)
                return

            self.metadata_widget.set_document(document_data)
            self.metadata_editor.set_initial_data(document_data)

        except Exception as e:
            print(f"ERROR: compact document selection failed: {e}")

    def on_compact_document_preview(self, doc_id):
        """
        Preview request from the compact list.
        Uses open_document_preview_by_id for consistency.
        """
        # Просто вызываем единый метод
        self.open_document_preview_by_id(doc_id)

    def setup_collapsible_tools_panel(self):
        """Настройка сворачиваемой панели инструментов."""
        # Основной виджет правой панели
        tools_main_widget = QWidget()
        tools_main_layout = QVBoxLayout()
        tools_main_layout.setContentsMargins(5, 5, 5, 5)
        
        # Панель управления сворачиванием
        collapse_panel = QWidget()
        collapse_layout = QHBoxLayout()
        collapse_layout.setContentsMargins(0, 0, 0, 0)
        
        self.collapse_title = QLabel("🛠 Инструменты")
        self.collapse_title.setStyleSheet("font-weight: bold; color: #2c3e50; font-size: 10pt;")
        
        # Кнопка сворачивания/разворачивания
        self.collapse_btn = QPushButton("◀")
        self.collapse_btn.setFixedSize(20, 20)
        self.collapse_btn.setToolTip("Свернуть панель")
        self.collapse_btn.setStyleSheet("""
            QPushButton {
                transition: all 0.2s ease;
                background-color: #34495e;
                color: white;
                border: none;
                border-radius: 3px;
                font-weight: bold;
                font-size: 8pt;
            }
            QPushButton:hover {
                transform: translateY(-1px);
                box-shadow: 0 2px 4px rgba(0,0,0,0.2);                        
                background-color: #2c3e50;
            }
        """)
        
        
        collapse_layout.addWidget(self.collapse_title)
        collapse_layout.addStretch()
        collapse_layout.addWidget(self.collapse_btn)
        collapse_panel.setLayout(collapse_layout)
        
        # Содержимое панели инструментов
        self.tools_content = QWidget()
        tools_layout = QVBoxLayout()
        tools_layout.setSpacing(8)
        
        # РљРќРћРџРљР С УМЕНЬШЕННЫМ РАЗМЕРОМ
        button_style = """
            QPushButton {
                transition: all 0.2s ease;
                color: white;
                border: none;
                padding: 10px 8px;
                font-size: 9pt;
                font-weight: bold;
                border-radius: 6px;
                text-align: center;
                min-height: 20px;
            }
            QPushButton:hover {
                transform: translateY(-1px);
                box-shadow: 0 2px 4px rgba(0,0,0,0.2);
                opacity: 0.8;
            }
        """
        # КНОПКА Р”РћР‘РђР’Р›Р•РќРРЇ ДОКУМЕНТА
        self.add_doc_btn = QPushButton("📝 Добавить")
        self.add_doc_btn.setStyleSheet(AppStyles.button_success())
        self.add_doc_btn.clicked.connect(self.show_add_document_dialog)

        # Кнопка управления справочниками
        self.manage_references_btn = QPushButton("👤 Справочники")
        self.manage_references_btn.setStyleSheet(AppStyles.button_primary())
        self.manage_references_btn.clicked.connect(self.show_reference_manager)
        
        # Кнопка поиска
        self.search_btn = QPushButton("🔍 Поиск")
        self.search_btn.setStyleSheet(AppStyles.button_primary())
        self.search_btn.clicked.connect(self.show_advanced_search)

        
        # Кнопка экспорта
        self.export_btn = QPushButton("📤 Экспорт")
        self.export_btn.setStyleSheet(AppStyles.button_warning())

        # === Р”РћР‘РђР’РРўР¬: РРЅС„РѕСЂРјР°С†РёСЏ о последней архивации ===
        backup_info_group = QGroupBox("📦 Архивация")
        backup_info_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #e0e0e0;
                border-radius: 5px;
                margin-top: 5px;
                padding-top: 10px;
                font-size: 9pt;
            }
        """)
        backup_info_layout = QVBoxLayout()

        # Получаем информацию о последней архивации
        backup_stats = self.db_manager.get_backup_statistics()
        last_backup = backup_stats.get('last_backup')
        days_since = backup_stats.get('days_since_backup')

        if last_backup:
            try:
                backup_date = datetime.strptime(last_backup, "%Y-%m-%d %H:%M:%S")
                formatted_date = backup_date.strftime("%d.%m.%Y")
            except:
                formatted_date = "Неизвестно"
            
            # Определяем цвет Рё иконку предупреждения
            if days_since is not None and days_since >= 7:
                icon = "⚠️"
                color = "#f44336"
                warning = f"\nПрошло {days_since} дней!"
            elif days_since is not None and days_since >= 5:
                icon = "⏰"
                color = "#ff9800"
                warning = f"\nПрошло {days_since} дней"
            else:
                icon = "✅"
                color = "#4caf50"
                warning = ""
            
            backup_text = f"{icon} {formatted_date}{warning}"
        else:
            backup_text = "⚠️ Не выполнялась"
            color = "#f44336"

        self.backup_status_label = QLabel(backup_text)
        self.backup_status_label.setStyleSheet(f"""
            QLabel {{
                color: {color};
                font-weight: bold;
                font-size: 8pt;
                padding: 5px;
                text-align: center;
            }}
        """)
        self.backup_status_label.setWordWrap(True)
        backup_info_layout.addWidget(self.backup_status_label)

        backup_info_group.setLayout(backup_info_layout)
        tools_layout.addWidget(backup_info_group)
        # Добавляем кнопки в лейаут
        tools_layout.addWidget(self.add_doc_btn)
        #tools_layout.addWidget(self.quick_add_btn)
        tools_layout.addWidget(self.manage_references_btn)
        tools_layout.addWidget(self.search_btn)
        #tools_layout.addWidget(self.stats_btn)
        tools_layout.addWidget(self.export_btn)
        #tools_layout.addWidget(self.settings_btn)
        tools_layout.addStretch()
        
        # РРЅС„РѕСЂРјР°С†РёСЏ о версии
        version_label = QLabel("📱 v2.1")
        version_label.setStyleSheet("""
            QLabel {
                color: #7f8c8d;
                font-size: 8pt;
                padding: 2px;
                text-align: center;
            }
        """)
        tools_layout.addWidget(version_label)

        self.diagnostics_btn = QPushButton("Внимание")
        self.diagnostics_btn.clicked.connect(self.show_diagnostics_dialog)
        self.diagnostics_btn.setToolTip("Открыть окно диагностики и журнал приложения")
        tools_layout.addWidget(self.diagnostics_btn)
        self._apply_diagnostics_button_style()
        
        self.tools_content.setLayout(tools_layout)
            # Р”РћР‘РђР’РРўР¬: Кнопка переключения БД
        self.switch_db_btn = QPushButton("🔄 База данных")
        self.switch_db_btn.setStyleSheet(button_style + """
            QPushButton { background-color: #17a2b8; }
            QPushButton:hover { background-color: #138496; }
        """)
        self.switch_db_btn.clicked.connect(self.show_database_switcher)
        self.export_btn.clicked.connect(self.show_export_dialog_new)
        # Р”РћР‘РђР’РРўР¬ в tools_layout (ПОСЛЕ других кнопок):
        tools_layout.addWidget(self.switch_db_btn)
        # Сборка правой панели
        tools_main_layout.addWidget(collapse_panel)
        tools_main_layout.addWidget(self.tools_content, 1)
        
        tools_main_widget.setLayout(tools_main_layout)
        self.right_dock.setWidget(tools_main_widget)
        
        # Состояние панели
        self.tools_panel_collapsed = False
    def show_export_dialog_new(self):
        """Показать новый диалог экспорта"""
        self.begin_background_operation("export", "Подготовка экспорта/архивации...")
        try:
            from export_manager import ExportDialog
            
            dialog = ExportDialog(self.db_manager, self)
            dialog.finished.connect(lambda _result: self.end_background_operation("export"))
            
            if hasattr(dialog, 'backup_completed'):
                dialog.backup_completed.connect(self.on_backup_completed)
            
            dialog.exec_()
            
        except Exception as e:
            self.end_background_operation("export")
            QMessageBox.critical(
                self,
                "❌ Ошибка",
                f"Не удалось открыть диалог экспорта:\n\n{str(e)}"
            )
            print(f"❌ Ошибка экспорта: {e}")
            import traceback
            traceback.print_exc()
    def update_backup_status(self, animate=True):
        """Обновить отображение статуса последней архивации"""
        try:
            backup_stats = self.db_manager.get_backup_statistics()
            last_backup = backup_stats.get('last_backup')
            days_since = backup_stats.get('days_since_backup')
            
            if last_backup:
                try:
                    backup_date = datetime.strptime(last_backup, "%Y-%m-%d %H:%M:%S")
                    formatted_date = backup_date.strftime("%d.%m.%Y")
                except:
                    formatted_date = "Неизвестно"
                
                # Определяем цвет Рё иконку
                if days_since is not None and days_since >= 7:
                    icon = "⚠️"
                    color = "#f44336"
                    warning = f"\nПрошло {days_since} дней!"
                elif days_since is not None and days_since >= 5:
                    icon = "⏰"
                    color = "#ff9800"
                    warning = f"\nПрошло {days_since} дней"
                else:
                    icon = "✅"
                    color = "#4caf50"
                    warning = ""
                
                backup_text = f"{icon} {formatted_date}{warning}"
            else:
                backup_text = "⚠️ Не выполнялась"
                color = "#f44336"
            
            if hasattr(self, 'backup_status_label'):
                self.backup_status_label.setText(backup_text)
                
                # ✅ Р”РћР‘РђР’РРўР¬: Анимация при обновлении
                if animate:
                    # Временно меняем цвет на яркий для привлечения внимания
                    self.backup_status_label.setStyleSheet(f"""
                        QLabel {{
                            color: white;
                            background-color: {color};
                            font-weight: bold;
                            font-size: 8pt;
                            padding: 5px;
                            text-align: center;
                            border-radius: 3px;
                        }}
                    """)
                    
                    # Возвращаем нормальный стиль через 1 секунду
                    from PyQt5.QtCore import QTimer
                    QTimer.singleShot(1000, lambda: self.backup_status_label.setStyleSheet(f"""
                        QLabel {{
                            color: {color};
                            font-weight: bold;
                            font-size: 8pt;
                            padding: 5px;
                            text-align: center;
                        }}
                    """))
                else:
                    # Обычный стиль без анимации
                    self.backup_status_label.setStyleSheet(f"""
                        QLabel {{
                            color: {color};
                            font-weight: bold;
                            font-size: 8pt;
                            padding: 5px;
                            text-align: center;
                        }}
                    """)
        
        except Exception as e:
            print(f"❌ Ошибка обновления статуса архивации: {e}")
    def on_backup_completed(self):
        """
        Обработчик завершения архивации
        
        Вызывается автоматически после успешной архивации для обновления UI
        """
        try:
            print("🔄 Обновление статуса архивации после успешного бэкапа...")
            
            # Обновляем статус в панели инструментов
            self.update_backup_status()
            
            # Опционально: Показываем уведомление
            # (можно закомментировать если не нужно)
            backup_stats = self.db_manager.get_backup_statistics()
            last_backup = backup_stats.get('last_backup')
            
            if last_backup:
                try:
                    from datetime import datetime
                    backup_date = datetime.strptime(last_backup, "%Y-%m-%d %H:%M:%S")
                    formatted_date = backup_date.strftime("%d.%m.%Y %H:%M")
                    
                    # Небольшое информационное сообщение (не блокирующее)
                    self.statusBar().showMessage(
                        f"✅ Архивация завершена: {formatted_date}",
                        5000  # Показывать 5 секунд
                    )
                except:
                    pass
            
            print("✅ Статус архивации обновлен")
            
        except Exception as e:
            print(f"❌ Ошибка обновления статуса архивации: {e}")
            import traceback
            traceback.print_exc()
    def check_backup_reminder(self):
        """Проверить необходимость напоминания об архивации"""
        try:
            backup_stats = self.db_manager.get_backup_statistics()
            days_since = backup_stats.get('days_since_backup')
            
            # Напоминаем если прошло 7+ дней или архивации не было
            if days_since is None or days_since >= 7:
                QMessageBox.warning(
                    self,
                    "⚠️ Напоминание об архивации",
                    f"Рекомендуется создать резервную копию базы данных!\n\n"
                    f"{'Последняя архивация не найдена' if days_since is None else f'Прошло {days_since} дней с последней архивации'}\n\n"
                    f"Используйте: Инструменты → Экспорт → Архивация"
                )
        except Exception as e:
            print(f"❌ Ошибка проверки напоминания: {e}")
    def open_document_preview(self, index):
        """
        Preview request from the main table.
        Uses QuickPreviewDialog.
        """
        try:
            selected_row = index.row()
            doc_id = self.documents_table_view.model()._data[selected_row][7]
            
            # РСЃРїРѕР»СЊР·СѓРµРј единый метод для всех предпросмотров
            self.open_document_preview_by_id(doc_id)
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self,
                "❌ Ошибка",
                f"Не удалось открыть предпросмотр:\n{str(e)}"
            )

    def toggle_tools_panel(self):
        """Сворачивание и разворачивание панели инструментов."""
        if self.tools_panel_collapsed:
            # Разворачиваем
            self.tools_content.setVisible(True)
            self.collapse_btn.setText("◀")
            self.collapse_btn.setToolTip("Свернуть панель")
            self.collapse_title.setVisible(True)
            
            # Возвращаем нормальную ширину
            self.right_dock.setMaximumWidth(300)
            self.right_dock.setMinimumWidth(200)
            
            self.tools_panel_collapsed = False
            print("🔧 Панель инструментов развернута")
        else:
            # Сворачиваем - НЕ СКРЫВАЕМ, А УМЕНЬШАЕМ
            self.tools_content.setVisible(False)
            self.collapse_btn.setText("▶")
            self.collapse_btn.setToolTip("Развернуть панель")
            self.collapse_title.setVisible(False)
            
            # Устанавливаем минимальную ширину
            self.right_dock.setMaximumWidth(30)
            self.right_dock.setMinimumWidth(30)
            
            self.tools_panel_collapsed = True
            print("🔧 Панель инструментов свернута")
    
    def setup_connections(self):
        #self.documents_filter_panel.search_signal.connect(self.filter_documents_with_filters)
        
        # Создаем диалог добавления документа
        self.dialog = AddDocumentDialog(self.db_manager, self.document_handler, self)
        self.dialog.document_added.connect(self.documents_table_view.load_documents)
        
        # Подключаем сигналы из таблицы документов - ТОЛЬКО РАЗ
        self.documents_table_view.show_metadata_tab.connect(self.open_metadata_editor_tab)
        self.documents_table_view.document_preview_requested.connect(self.open_document_preview_by_id)
        self.documents_table_view.document_selected.connect(self.on_document_selected)
        self.documents_table_view.open_file_requested.connect(self.open_file_by_document_id)
        if self.documents_table_view.selectionModel():
            self.documents_table_view.selectionModel().selectionChanged.connect(self.on_document_selection_changed)
        
        # Подключаем сигналы компактной таблицы
        self.compact_documents_table.document_selected.connect(self.on_compact_document_selected)
        self.compact_documents_table.document_preview_requested.connect(self.on_compact_document_preview)
        self.compact_documents_table.open_file_requested.connect(self.open_file_by_document_id)
        # Подключаем сигналы редакторов метаданных
        self.metadata_editor.metadata_saved.connect(self.on_metadata_saved)
        self.metadata_widget.metadata_saved.connect(self.on_metadata_saved)
        
        
        print("✅ Все сигналы подключены")
    
    def filter_documents(self, keyword):
        """Фильтрация документов (твой метод)"""
        filtered_docs = self.db_manager.search_documents(keyword)
        self.update_documents_table(filtered_docs)
    def on_document_selected(self, doc_id):
        """Handle document selection in the main table."""
        try:
            print(f"INFO: on_document_selected doc_id={doc_id}")
            self.current_document_id = doc_id

            document_data = self.db_manager.get_document_by_id(doc_id)
            if not document_data:
                self._handle_remote_document_missing(doc_id)
                return

            if not hasattr(self, 'metadata_widget'):
                print("ERROR: metadata_widget is missing")
                return

            self.metadata_widget.set_document(document_data)
            self.metadata_editor.set_initial_data(document_data)

        except Exception as e:
            print(f"ERROR: document selection failed: {e}")
            import traceback
            traceback.print_exc()

    def update_documents_table(self, documents):
        """Update main and compact tables with the same search result."""
        try:
            self.documents_table_view.update_documents(documents)

            compact_data = [
                [
                    doc.get('title') or "Untitled",
                    doc.get('reg_number') or "-",
                    doc.get('reg_date') or "-",
                    doc.get('id', ''),
                ]
                for doc in documents
            ]
            self.compact_documents_table.update_data(compact_data)
            self.update_compact_table_count()
        except Exception as e:
            print(f"Table update error: {e}")

    def show_add_document_dialog(self):
        """Показ диалога добавления документа"""
        # Создаем диалог БЕЗ предварительного выбора файла
        dialog = AddDocumentDialog(self.db_manager, self.document_handler, self, file_path=None)
        dialog.document_added.connect(self.documents_table_view.load_documents)
        if dialog.exec_() == QDialog.Accepted:
            # Обновляем таблицу документов после добавления
            self.documents_table_view.load_documents()

    # =============== Новые асинхронные методы ===============

    

    
    def show_reference_manager(self):
        """Показать окно управления справочниками"""
        try:
            # Проверяем, не открыто ли уже окно
            if hasattr(self, 'reference_manager_dialog') and self.reference_manager_dialog.isVisible():
                # Если открыто, просто активируем его
                self.reference_manager_dialog.raise_()
                self.reference_manager_dialog.activateWindow()
                return
            
            print("🛠 Открываем менеджер справочников...")
            
            # Создаем новое окно
            self.reference_manager_dialog = ReferenceManagerDialog(self.db_manager, self)
            
            # Подключаем сигнал обновления данных
            self.reference_manager_dialog.references_updated.connect(self.on_references_updated)
            
            # Показываем окно
            self.reference_manager_dialog.show()
            
            print("✅ Менеджер справочников открыт")
            
        except Exception as e:
            print(f"❌ Ошибка открытия менеджера справочников: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self, 
                "Ошибка", 
                f"Не удалось открыть менеджер справочников:\n{str(e)}"
            )

    def on_references_updated(self):
        """
        ⭐ Обработчик обновления справочников
        
        Вызывается когда пользователь изменил справочники через окно "Управление справочниками"
        Автоматически обновляет списки во всех редакторах метаданных
        """
        print(f"\n{'='*60}")
        print("🔄 ОБНОВЛЕНИЕ СПРАВОЧНИКОВ В РЕДАКТОРАХ")
        print(f"{'='*60}")
        
        try:
            # 1пёЏ⃣ Обновляем полный редактор метаданных
            if hasattr(self, 'metadata_editor'):
                print("📝 Обновление полного редактора метаданных...")
                if hasattr(self.metadata_editor, 'reload_references'):
                    self.metadata_editor.reload_references()
                    print("✅ Полный редактор обновлен")
                else:
                    print("⚠️ Метод reload_references не найден в полном редакторе")
            else:
                print("⚠️ Полный редактор метаданных не найден")
            if hasattr(self, 'tag_search_widget') and self.tag_search_widget:
                self.tag_search_widget.clear_reference_cache()
            # 2пёЏ⃣ Обновляем компактный редактор метаданных
       
            if hasattr(self, 'metadata_widget'):
                print("📋 Обновление компактного редактора метаданных...")
                if hasattr(self.metadata_widget, 'reload_references'):
                    self.metadata_widget.reload_references()
                    print("✅ Компактный редактор обновлен")
                else:
                    print("⚠️ Метод reload_references не найден в компактном редакторе")
            else:
                print("⚠️ Компактный редактор метаданных не найден")
            
            print(f"{'='*60}")
            print("✅ ВСЕ РЕДАКТОРЫ УСПЕШНО ОБНОВЛЕНЫ")
            print(f"{'='*60}\n")
            
        except Exception as e:
            print(f"\n{'='*60}")
            print(f"❌ ОШИБКА ОБНОВЛЕНИЯ РЕДАКТОРОВ")
            print(f"   {str(e)}")
            print(f"{'='*60}\n")
            import traceback
            traceback.print_exc()

    def show_statistics(self):
        """Показать статистику базы данных"""
        try:
            stats = self.db_manager.get_documents_statistics()
            
            message = "📊 СТАТИСТИКА БАЗЫ ДАННЫХ\n\n"
            message += f"📄 Всего документов: {stats.get('total_documents', 0)}\n\n"
            
            # Статистика по статусам
            by_status = stats.get('by_status', {})
            if by_status:
                message += "📋 По статусам:\n"
                for status, count in by_status.items():
                    message += f"  • {status}: {count}\n"
                message += "\n"
            
            # Статистика по типам
            by_type = stats.get('by_type', {})
            if by_type:
                message += "📑 По типам документов:\n"
                for doc_type, count in by_type.items():
                    message += f"  • {doc_type}: {count}\n"
                message += "\n"
            
            # Топ исполнителей
            by_executor = stats.get('by_executor', {})
            if by_executor:
                message += "👤 Топ исполнителей:\n"
                for executor, count in list(by_executor.items())[:5]:
                    message += f"  • {executor}: {count} док.\n"
            
            QMessageBox.information(self, "📊 Статистика", message)
            
        except Exception as e:
            print(f"❌ Ошибка получения статистики: {e}")
            QMessageBox.warning(self, "Ошибка", f"Не удалось получить статистику: {str(e)}")
    def on_metadata_saved(self, document_id):
        """Обработчик сохранения метаданных из любого редактора"""
        # Обновляем другой редактор если он открыт
        if self.central_widget.currentIndex() == 0:  # Если активна вкладка списка
            self.metadata_widget.set_document(
                self.db_manager.get_document_by_id(document_id)
            )
        elif self.central_widget.currentIndex() == 1:  # Если активен редактор
            self.metadata_editor.set_initial_data(
                self.db_manager.get_document_by_id(document_id)
            )
        
        # Обновляем таблицы с текущими фильтрами
        filters = self.get_current_filters()
        if filters and any(filters.values()):
            self.documents_table_view.load_documents(filters)
            if self.current_compact_mode == 1:
                self.load_quick_access_documents()
            else:
                self.compact_documents_table.load_documents(filters)
            self.update_compact_table_count()
        else:
            self.documents_table_view.load_documents()
            if self.current_compact_mode == 1:
                self.load_quick_access_documents()
            else:
                self.compact_documents_table.load_documents()
            self.update_compact_table_count()
    def get_current_filters(self):
        if hasattr(self, "documents_table_view"):
            filters = getattr(self.documents_table_view, "current_filters", None)
            return filters if isinstance(filters, dict) else {}
        return {}
    def on_document_selection_changed(self, selected, deselected):
        """Обработчик изменения выбора документа в основной таблице"""
        try:
            selection_model = self.documents_table_view.selectionModel()
            if not selection_model.hasSelection():
                # Если ничего не выбрано - очищаем метаданные
                self.metadata_widget.clear_form()
                return
            
            # Получаем выбранную строку
            selected_indexes = selection_model.selectedRows()
            if not selected_indexes:
                return
            
            # Берем первую выбранную строку
            index = selected_indexes[0]
            row = index.row()
            
            # Получаем ID документа из первой колонки
            doc_id = self.documents_table_view.model()._data[row][7]
            
            # Загружаем метаданные в левую панель, если активна вкладка списка
            #if self.central_widget.currentIndex() == 0:
            #    self.show_document_metadata(doc_id)
                
        except Exception as e:
            print(f"❌ Ошибка при обработке выбора документа: {e}")
    def open_metadata_editor_tab(self, doc_id):
        """Switch to the metadata editor tab for a document."""
        try:
            print(f"INFO: open metadata editor for doc_id={doc_id}")

            document_data = self.db_manager.get_document_by_id(doc_id)
            if document_data:
                self.current_document_id = doc_id
                self.central_widget.setCurrentIndex(1)
                self.metadata_editor.set_initial_data(document_data)
                print(f"INFO: metadata editor opened for document {doc_id}")
            else:
                self._handle_remote_document_missing(doc_id)

        except Exception as e:
            print(f"ERROR: failed to open metadata editor: {e}")
            import traceback
            traceback.print_exc()
    

    def on_quick_preview_closed(self, result):
        """Обработчик закрытия быстрого предпросмотра"""
        if hasattr(self, '_quick_preview_dialog'):
            self._quick_preview_dialog.deleteLater()
            delattr(self, '_quick_preview_dialog')


    def on_preview_dialog_closed(self, result):
        """Обработчик закрытия диалога предпросмотра"""
        # Удаляем ссылку на диалог после его закрытия
        if hasattr(self, '_preview_dialog'):
            self._preview_dialog.deleteLater()
            delattr(self, '_preview_dialog')
    def show_document_metadata(self, document_id):
        """Show metadata in the compact panel by document ID."""
        try:
            document_data = self.db_manager.get_document_by_id(document_id)
            if document_data:
                self.current_document_id = document_id
                self.metadata_widget.set_document(document_data)
            else:
                self._handle_remote_document_missing(document_id)
        except Exception as e:
            print(f"ERROR: failed to load document metadata: {e}")
            self.metadata_widget.clear_form()
    # Search block
    


    def filter_documents_with_filters(self, filters):
        """Фильтрация с синхронизацией"""
        try:
            self.documents_table_view.load_documents(filters)
            self.compact_documents_table.load_documents(filters)
            self.update_compact_table_count()
            print(f"🔍 Применены фильтры")
        except Exception as e:
            print(f"❌ Ошибка фильтрации: {e}")
    def sync_document_lists(self):
        try:
            if hasattr(self, "documents_table_view"):
                self.documents_table_view.refresh_loaded_documents()

            if hasattr(self, "compact_documents_table"):
                filters = self.get_current_filters()
                if getattr(self, "current_compact_mode", 0) == 1:
                    self.load_quick_access_documents()
                elif filters and any(filters.values()):
                    self.compact_documents_table.load_documents(filters)
                else:
                    self.compact_documents_table.load_documents()
                self.update_compact_table_count()
        except Exception as e:
            print(f"❌ Ошибка синхронизации: {e}")
    def update_compact_table_count(self):
        """Обновление счетчика документов"""
        model = self.compact_documents_table.model()
        count = model.rowCount() if model else 0
        self.documents_count_label.setText(f"📊 Документов: {count}")

    def on_compact_mode_changed(self, index):
        """
        Обработчик переключения режимов компактной таблицы
        
        Args:
            index: 0 = Список документов, 1 = Быстрый доступ
        """
        try:
            self.current_compact_mode = index
            
            if index == 0:
                # Режим "Список документов" - загружаем последние 100
                print("📋 Переключение на режим: Список документов")
                filters = self.get_current_filters()
                if filters and any(filters.values()):
                    self.compact_documents_table.load_documents(filters)
                else:
                    self.compact_documents_table.load_documents()
                self.update_compact_table_count()
                
            elif index == 1:
                # Режим "Быстрый доступ" - загружаем из списка быстрого доступа
                print("⚡ Переключение на режим: Быстрый доступ")
                self.load_quick_access_documents()
            self.save_ui_state()
                
        except Exception as e:
            print(f"❌ Ошибка переключения режима: {e}")
            import traceback
            traceback.print_exc()

    def load_quick_access_documents(self):
        """Загрузка документов из списка быстрого доступа"""
        try:
            if not self.quick_access_documents:
                # Список пустой - показываем пустую таблицу
                self.compact_documents_table.update_data([])
                self.update_compact_table_count()
                print("⚡ Список быстрого доступа пуст")
                return

            documents = self.db_manager.get_compact_documents(
                document_ids=self.quick_access_documents,
                limit=max(len(self.quick_access_documents), 1),
            )
            table_data = self.compact_documents_table._format_table_data(documents)
            self.compact_documents_table.update_data(table_data)
            self.update_compact_table_count()
            print(f"⚡ Загружено документов в быстрый доступ: {len(table_data)}")
            
        except Exception as e:
            print(f"❌ Ошибка загрузки быстрого доступа: {e}")
            import traceback
            traceback.print_exc()

    def add_to_quick_access(self, doc_id):
        """
        Добавить документ в быстрый доступ
        
        Args:
            doc_id: ID документа
        """
        try:
            if doc_id not in self.quick_access_documents:
                self.quick_access_documents.append(doc_id)
                print(f"✅ Документ ID {doc_id} добавлен в быстрый доступ")
                self.save_ui_state()
                
                # Если текущий режим - быстрый доступ, обновляем список
                if self.current_compact_mode == 1:
                    self.load_quick_access_documents()
                
                QMessageBox.information(
                    self,
                    "✅ Успешно",
                    "Документ добавлен в быстрый доступ"
                )
            else:
                QMessageBox.information(
                    self,
                    "ℹ️ Информация",
                    "Документ уже находится в списке быстрого доступа"
                )
                
        except Exception as e:
            print(f"❌ Ошибка добавления в быстрый доступ: {e}")
            QMessageBox.critical(
                self,
                "❌ Ошибка",
                f"Не удалось добавить документ:\n{str(e)}"
            )
    def add_selected_to_quick_access(self):
        """
        Добавить все выделенные документы в быстрый доступ
        """
        try:
            # Получаем выделенные строки из основной таблицы
            if not hasattr(self, 'documents_table_view'):
                QMessageBox.warning(
                    self,
                    "⚠️ Предупреждение",
                    "Таблица документов не доступна"
                )
                return
            
            selection_model = self.documents_table_view.selectionModel()
            
            if not selection_model or not selection_model.hasSelection():
                QMessageBox.information(
                    self,
                    "ℹ️ Информация",
                    "Не выбрано ни одного документа.\n\n"
                    "Выделите документы в основной таблице и повторите попытку."
                )
                return
            
            # Получаем список выделенных строк
            selected_rows = selection_model.selectedRows()
            
            if not selected_rows:
                QMessageBox.information(
                    self,
                    "ℹ️ Информация",
                    "Не выбрано ни одного документа"
                )
                return
            
            # Собираем ID документов
            selected_doc_ids = []
            for index in selected_rows:
                row = index.row()
                # ID документа находится в последней колонке (индекс 7)
                doc_id = self.documents_table_view.model()._data[row][7]
                selected_doc_ids.append(doc_id)
            
            # Подтверждение
            reply = QMessageBox.question(
                self,
                "📌 Подтверждение",
                f"Добавить {len(selected_doc_ids)} документов в быстрый доступ?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            
            if reply != QMessageBox.Yes:
                return
            
            # Добавляем документы
            added_count = 0
            skipped_count = 0
            
            for doc_id in selected_doc_ids:
                if doc_id not in self.quick_access_documents:
                    self.quick_access_documents.append(doc_id)
                    added_count += 1
                else:
                    skipped_count += 1
            if added_count > 0:
                self.save_ui_state()
            
            # Если текущий режим - быстрый доступ, обновляем список
            if self.current_compact_mode == 1:
                self.load_quick_access_documents()
            
            # Сообщение о результате
            message = f"✅ Добавлено документов: {added_count}"
            if skipped_count > 0:
                message += f"\nℹ️ Уже были в списке: {skipped_count}"
            
            QMessageBox.information(
                self,
                "✅ Готово",
                message
            )
            
            print(f"✅ Добавлено {added_count} документов в быстрый доступ (пропущено: {skipped_count})")
            
        except Exception as e:
            print(f"❌ Ошибка массового добавления: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self,
                "❌ Ошибка",
                f"Не удалось добавить документы:\n{str(e)}"
            )
    def remove_from_quick_access(self, doc_id):
        """
        Удалить документ из быстрого доступа
        
        Args:
            doc_id: ID документа
        """
        try:
            if doc_id in self.quick_access_documents:
                self.quick_access_documents.remove(doc_id)
                print(f"✅ Документ ID {doc_id} удален из быстрого доступа")
                self.save_ui_state()
                
                # Обновляем список
                if self.current_compact_mode == 1:
                    self.load_quick_access_documents()
                
        except Exception as e:
            print(f"❌ Ошибка удаления из быстрого доступа: {e}")

    def clear_quick_access(self):
        """Очистить весь список быстрого доступа"""
        try:
            reply = QMessageBox.question(
                self,
                "⚠️ Подтверждение",
                "Вы уверены, что хотите очистить весь список быстрого доступа?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                self.quick_access_documents.clear()
                print("🗑️ Список быстрого доступа очищен")
                self.save_ui_state()
                
                # Обновляем список
                if self.current_compact_mode == 1:
                    self.load_quick_access_documents()
                
                QMessageBox.information(
                    self,
                    "✅ Успешно",
                    "Список быстрого доступа очищен"
                )
                
        except Exception as e:
            print(f"❌ Ошибка очистки быстрого доступа: {e}")

    def export_quick_access_documents(self):
        """Экспорт документов из быстрого доступа"""
        try:
            if not self.quick_access_documents:
                QMessageBox.warning(
                    self,
                    "⚠️ Предупреждение",
                    "Список быстрого доступа пуст"
                )
                return
            
            # Получаем полные данные документов
            documents_data = []
            for doc_id in self.quick_access_documents:
                doc_data = self.db_manager.get_document_by_id(doc_id)
                if doc_data:
                    documents_data.append(doc_data)
            
            if not documents_data:
                QMessageBox.warning(
                    self,
                    "⚠️ Предупреждение",
                    "Не удалось загрузить данные документов"
                )
                return
            
            # Показываем информационное сообщение
            reply = QMessageBox.question(
                self,
                "📤 Экспорт документов",
                f"Будет выполнен экспорт документов из быстрого доступа.\n\n"
                f"Документов для экспорта: {len(documents_data)}\n\n"
                f"Вы сможете выбрать нужные поля Рё настройки экспорта.",
                QMessageBox.Ok | QMessageBox.Cancel
            )
            
            if reply != QMessageBox.Ok:
                return
            
            # Открываем диалог экспорта с предустановленными документами
            from export_manager import ExportDialog
            dialog = ExportDialog(self.db_manager, self)
            
            # ✅ КЛЮЧЕВОЕ РР—РњР•РќР•РќРР•: Устанавливаем документы для экспорта
            dialog.set_documents_for_export(documents_data, mode='selected')
            
            dialog.exec_()
            
            print(f"📤 Открыт диалог экспорта с {len(documents_data)} документами из быстрого доступа")
            
        except Exception as e:
            print(f"❌ Ошибка экспорта: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self,
                "❌ Ошибка",
                f"Не удалось открыть диалог экспорта:\n{str(e)}"
            )

