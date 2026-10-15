from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *
from datetime import datetime
import re
from ui_styles import AppColors, AppStyles, AppLayout
from document_utils import parse_flexible_datetime
from metadata_form import ReferenceManager

try:
    from database_optimized import DocumentConflictError
except Exception:
    class DocumentConflictError(Exception):
        pass

class CollapsibleSection(QWidget):
    """Сворачиваемая секция для экономии места"""
    
    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.toggle_button = QPushButton(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(True)
        self.toggle_button.setStyleSheet(f"""
            QPushButton {{
                text-align: left;
                padding: 6px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {AppColors.GRAY_100}, stop:1 {AppColors.GRAY_200});
                border: 1px solid {AppColors.GRAY_300};
                border-radius: 4px;
                font-weight: bold;
                font-size: 9pt;
                color: {AppColors.TEXT_PRIMARY};
            }}
            QPushButton:checked {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {AppColors.PRIMARY}, stop:1 {AppColors.PRIMARY_DARK});
                color: white;
            }}
            QPushButton:hover {{
                border-color: {AppColors.PRIMARY};
            }}
        """)
        self.content_area = QWidget()
        self.content_layout = QFormLayout()
        self.content_layout.setVerticalSpacing(3)
        self.content_layout.setContentsMargins(8, 4, 8, 4)
        self.content_area.setLayout(self.content_layout)
        
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.toggle_button)
        layout.addWidget(self.content_area)
        self.setLayout(layout)
        
        self.toggle_button.toggled.connect(self.toggle_content)
        
    def toggle_content(self, checked):
        """Переключить видимость контента"""
        self.content_area.setVisible(checked)
        arrow = "▼ " if checked else "▶ "
        text = self.toggle_button.text()
        if text.startswith(("▼ ", "▶ ")):
            text = text[2:]
        self.toggle_button.setText(arrow + text)
    
    def add_field(self, label, widget):
        """Добавить поле в секцию"""
        self.content_layout.addRow(label, widget)
        
    def add_widget(self, widget):
        """Добавить виджет в секцию"""
        self.content_layout.addRow(widget)


class CompactMetadataEditor(QWidget):
    """Оптимизированный компактный редактор метаданных 400x870"""
    
    metadata_saved = pyqtSignal(int)
    
    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db_manager = db_manager
        self.reference_manager = ReferenceManager(db_manager) if db_manager else None
        self.current_document_id = None
        self._loaded_snapshot = None
        self.setFixedWidth(500)
        self.setMinimumHeight(870)
        self.setMaximumHeight(900)
        self.init_ui()
        self.apply_modern_styles()
    
    def init_ui(self):
        """Инициализация оптимизированного интерфейса"""
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(4)
        
        # Заголовок
        self.header_label = QLabel("📋 Метаданные")
        self.header_label.setAlignment(Qt.AlignCenter)
        self.header_label.setStyleSheet(f"""
            QLabel {{
                font-size: 11pt;
                font-weight: bold;
                color: white;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {AppColors.PRIMARY}, stop:1 {AppColors.PRIMARY_DARK});
                padding: 8px;
                border-radius: 6px;
                margin-bottom: 4px;
            }}
        """)
        
        # Название документа
        self.title_display = QLabel("Документ не выбран")
        self.title_display.setWordWrap(True)
        self.title_display.setAlignment(Qt.AlignCenter)
        self.title_display.setMaximumWidth(500)
        self.title_display.setMaximumHeight(60)
        self.title_display.setMinimumHeight(30)
        self.title_display.setStyleSheet(AppStyles.lable())
        
        # Прокручиваемая область
        scroll_area = QScrollArea()
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                background: #f8f9fa;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #6c757d;
                border-radius: 4px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: #495057;
            }
        """)
        
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout()
        scroll_layout.setContentsMargins(0, 0, 8, 0)
        scroll_layout.setSpacing(6)
        
        # === ОСНОВНАЯ ИНФОРМАЦИЯ ===
        self.status_field = self.create_compact_combo(["-- Загрузка --"])
        self.type_field = self.create_compact_combo(["-- Загрузка --"])
        self.document_kind_field = self.create_compact_combo(["-- Загрузка --"])
        self.signing_type_field = self.create_compact_combo(["-- Загрузка --"])
        self.status_widget = self.create_compact_reference_field(self.status_field, 'status')
        self.type_widget = self.create_compact_reference_field(self.type_field, 'document_types')
        self.document_kind_widget = self.create_compact_reference_field(self.document_kind_field, 'document_kinds')
        self.signing_type_widget = self.create_compact_reference_field(self.signing_type_field, 'signing_types')
        main_section = CollapsibleSection("📄 Основная информация")
        self.reg_number_field = self.create_compact_line_edit("Введите номер")

        # Дополнительный номер
        self.number_field = self.create_compact_line_edit("Доп. номер")

        self.reg_date_field = self.create_compact_date_edit()

        self.executor_field = self.create_compact_combo(["-- Загрузка --"])
        self.theme_field = self.create_compact_combo(["-- Загрузка --"])
        self.executor_widget = self.create_compact_reference_field(self.executor_field, 'executors')
        self.theme_widget = self.create_compact_reference_field(self.theme_field, 'themes')

        # Заголовок
        self.title_field = self.create_compact_line_edit("Заголовок")

        # Количество листов
        self.pages_count_field = QSpinBox()
        self.pages_count_field.setMinimum(0)
        self.pages_count_field.setMaximum(9999)
        self.pages_count_field.setSuffix(" л.")
        self.pages_count_field.setFixedHeight(24)
        self.pages_count_field.setStyleSheet("""
            QSpinBox {
                border: 1px solid #ced4da;
                border-radius: 3px;
                padding: 2px 6px;
                background: white;
                font-size: 8pt;
            }
            QSpinBox:focus {
                border-color: #007bff;
            }
        """)

        # Количество приложений
        self.attachments_count_field = QSpinBox()
        self.attachments_count_field.setMinimum(0)
        self.attachments_count_field.setMaximum(999)
        self.attachments_count_field.setSuffix(" шт.")
        self.attachments_count_field.setFixedHeight(24)
        self.attachments_count_field.setStyleSheet("""
            QSpinBox {
                border: 1px solid #ced4da;
                border-radius: 3px;
                padding: 2px 6px;
                background: white;
                font-size: 8pt;
            }
            QSpinBox:focus {
                border-color: #007bff;
            }
        """)

        main_section.add_field("Статус:", self.status_widget)
        main_section.add_field("Тип:", self.type_widget)
        main_section.add_field("Вид:", self.document_kind_widget)
        main_section.add_field("Подписание:", self.signing_type_widget)
        main_section.add_field("Рег. №:", self.reg_number_field)
        main_section.add_field("Доп. №:", self.number_field)
        main_section.add_field("Дата рег.:", self.reg_date_field)
        main_section.add_field("Исполнитель:", self.executor_widget)
        main_section.add_field("Тема:", self.theme_widget)
        main_section.add_field("Заголовок:", self.title_field)
        main_section.add_field("Листов:", self.pages_count_field)
        main_section.add_field("Приложений:", self.attachments_count_field)
        
        # Добавляем секцию в layout
        scroll_layout.addWidget(main_section)
        scroll_layout.addStretch()
        
        scroll_widget.setLayout(scroll_layout)
        scroll_area.setWidget(scroll_widget)
        scroll_area.setWidgetResizable(True)
        
        # Кнопки управления
        buttons_widget = QWidget()
        buttons_widget.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                                          stop: 0 #f8f9fa, stop: 1 #e9ecef);
                border-top: 1px solid #dee2e6;
                border-radius: 4px;
            }
        """)
        buttons_layout = QHBoxLayout()
        # ✅ ДОБАВИТЬ: Кнопка быстрого просмотра
        self.quick_preview_btn = QPushButton("Просмотр")
        self.quick_preview_btn.setStyleSheet(AppStyles.button_primary())
        self.quick_preview_btn.clicked.connect(self.open_quick_preview)
        font = self.quick_preview_btn.font()
        font.setPointSize(7)  # Уменьши шрифт
        self.quick_preview_btn.setFont(font)
        self.quick_preview_btn.setEnabled(False)
        self.quick_preview_btn.setMinimumHeight(36)  # Добавь высоту

        # ✅ ДОБАВИТЬ: Кнопка открытия в Word  
        self.open_file_btn = QPushButton("Открыть")
        self.open_file_btn.setStyleSheet(AppStyles.button_primary())
        self.open_file_btn.clicked.connect(self.open_in_word)
        self.open_file_btn.setEnabled(False)
        self.open_file_btn.setMinimumHeight(36)  # Добавь высоту
        
        self.save_btn = QPushButton("💾")
        self.save_btn.setToolTip("Сохранить изменения")
        self.save_btn.setStyleSheet(self.get_button_style("#28a745", "#1E7C32"))
        
        self.save_btn.clicked.connect(self.save_metadata)
        self.save_btn.setEnabled(False)

        self.reset_btn = QPushButton("🔄")
        self.reset_btn.setToolTip("Сбросить изменения") 
        self.reset_btn.setStyleSheet(self.get_button_style("#ffc107", "#e0a800"))
        
        self.reset_btn.clicked.connect(self.reset_form)
        self.reset_btn.setEnabled(False)
        
        # Статус бар
        self.status_bar = QLabel("Готов к работе")
        self.status_bar.setStyleSheet("""
            QLabel {
                background: transparent;
                color: #6c757d;
                font-size: 8pt;
                padding: 2px;
            }
        """)
        # ✅ ДОБАВИТЬ кнопки в layout
        buttons_layout.addWidget(self.quick_preview_btn)
        buttons_layout.addWidget(self.open_file_btn)
        buttons_layout.addStretch()
        buttons_layout.addWidget(self.status_bar)
        buttons_layout.addStretch()
        buttons_layout.addWidget(self.save_btn)
        buttons_layout.addWidget(self.reset_btn)
        buttons_widget.setLayout(buttons_layout)
        
        # Сборка главного лайаута
        main_layout.addWidget(self.header_label)
        main_layout.addWidget(self.title_display)
        main_layout.addWidget(scroll_area, 1)
        main_layout.addWidget(buttons_widget)
        
        self.setLayout(main_layout)
        
        # Загружаем справочники
        self.load_references()
        self.connect_change_signals()
    
    def create_compact_combo(self, items):
        """Создать компактный combobox"""
        combo = QComboBox()
        combo.addItems(items)
        combo.setFixedHeight(24)
        combo.setStyleSheet("""
            QComboBox {
                border: 1px solid #ced4da;
                border-radius: 3px;
                padding: 2px 6px;
                background: white;
                font-size: 8pt;
            }
            QComboBox:focus {
                border-color: #007bff;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox::down-arrow {
                image: url(data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTIiIGhlaWdodD0iOCIgdmlld0JveD0iMCAwIDEyIDgiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxwYXRoIGQ9Ik0xIDFMNiA2TDExIDEiIHN0cm9rZT0iIzZjNzU3ZCIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz4KPC9zdmc+);
                width: 12px;
                height: 8px;
            }
            QComboBox QAbstractItemView {
                border: 1px solid #ced4da;
                background: white;
                selection-background-color: #007bff;
                font-size: 8pt;
            }
        """)
        return combo

    def create_compact_reference_field(self, combo, ref_type):
        """Создать строку справочного поля с inline-кнопкой добавления."""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        add_button = QPushButton("+")
        add_button.setToolTip("Добавить новое значение в справочник")
        add_button.setCursor(Qt.PointingHandCursor)
        add_button.setFixedSize(26, 26)
        add_button.setStyleSheet("""
            QPushButton {
                border: 1px solid #0d419d;
                border-radius: 4px;
                background: #1f6feb;
                color: white;
                font-size: 12pt;
                font-weight: bold;
                padding-bottom: 1px;
            }
            QPushButton:hover {
                background: #1858bd;
            }
            QPushButton:pressed {
                background: #144b9f;
            }
        """)
        add_button.clicked.connect(lambda _=False, rt=ref_type, cb=combo: self.quick_add_reference_value(rt, cb))
        layout.addWidget(combo, 1)
        layout.addWidget(add_button)
        return container

    def quick_add_reference_value(self, ref_type, combo):
        """Добавить значение в справочник из поля выбора и сразу выбрать его."""
        if not self.reference_manager:
            QMessageBox.warning(self, "Ошибка", "Справочники недоступны для текущей базы.")
            return

        initial_text = combo.currentText().strip()
        if initial_text.startswith("-- "):
            initial_text = ""

        new_name, ok = QInputDialog.getText(
            self,
            "Добавить значение",
            "Название:",
            QLineEdit.Normal,
            initial_text
        )
        if not ok:
            return

        new_name = (new_name or "").strip()
        if not new_name:
            QMessageBox.warning(self, "Пустое значение", "Введите непустое название.")
            return

        item_id, created, reactivated = self.reference_manager.add_reference_item(ref_type, new_name)
        if item_id is None:
            QMessageBox.critical(self, "Ошибка", "Не удалось добавить значение в справочник.")
            return

        loader_map = {
            'status': self.load_statuses,
            'document_types': self.load_document_types,
            'document_kinds': self.load_document_kinds,
            'signing_types': self.load_signing_types,
            'executors': self.load_executors,
            'themes': self.load_themes,
        }
        loader = loader_map.get(ref_type)
        if loader:
            loader()

        self.set_combo_by_id(combo, item_id)
        self.on_field_changed()
        self._refresh_external_reference_views()

        if reactivated:
            self.status_bar.setText("Запись восстановлена и выбрана")
        elif not created:
            self.status_bar.setText("Запись уже была в справочнике и выбрана")
        else:
            self.status_bar.setText("Новое значение добавлено")

    def _refresh_external_reference_views(self):
        """Обновить справочники в других редакторах главного окна."""
        main_window = self.get_main_window()
        if not main_window:
            return

        full_editor = getattr(main_window, 'metadata_editor', None)
        if full_editor is not None and full_editor is not self and hasattr(full_editor, 'reload_references'):
            try:
                full_editor.reload_references()
            except Exception as e:
                print(f"⚠️ Не удалось обновить полный редактор: {e}")

        if hasattr(main_window, 'tag_search_widget'):
            tag_widget = main_window.tag_search_widget
            if hasattr(tag_widget, 'clear_reference_cache'):
                try:
                    tag_widget.clear_reference_cache()
                except Exception as e:
                    print(f"⚠️ Не удалось очистить кеш тегов: {e}")
    
    def create_compact_line_edit(self, placeholder=""):
        """Создать компактный line edit"""
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setFixedHeight(24)
        edit.setMaximumWidth(350)
        edit.setStyleSheet("""
            QLineEdit {
                border: 1px solid #ced4da;
                border-radius: 3px;
                padding: 2px 6px;
                background: white;
                font-size: 8pt;
            }
            QLineEdit:focus {
                border-color: #007bff;
                box-shadow: 0 0 0 2px rgba(0,123,255,.25);
            }
        """)
        return edit
    
    def create_compact_date_edit(self):
        """Создать компактный date edit"""
        date_edit = QDateEdit()
        date_edit.setCalendarPopup(True)
        date_edit.setDisplayFormat("dd.MM.yyyy")
        date_edit.setKeyboardTracking(False)
        date_edit.setFixedHeight(24)
        date_edit.setStyleSheet("""
            QDateEdit {
                border: 1px solid #ced4da;
                border-radius: 3px;
                padding: 2px 6px;
                background: white;
                font-size: 8pt;
            }
            QDateEdit:focus {
                border-color: #007bff;
            }
            QDateEdit::drop-down {
                border: none;
                width: 20px;
            }
            QDateEdit::down-arrow {
                width: 12px;
                height: 8px;
            }
        """)
        editor = date_edit.lineEdit()
        if editor:
            editor.setInputMask("00.00.0000;_")
            editor.setPlaceholderText("дд.мм.гггг")
            editor.setProperty("_compact_date_owner", date_edit)
            editor.installEventFilter(self)
            editor.setContextMenuPolicy(Qt.CustomContextMenu)
            editor.customContextMenuRequested.connect(
                lambda pos, de=date_edit, ed=editor: self._show_compact_date_context_menu(
                    de, ed, ed.mapToGlobal(pos)
                )
            )
            editor.editingFinished.connect(lambda de=date_edit: self._validate_compact_date_field(de))

        def wheelEvent(event):
            event.ignore()

        date_edit.wheelEvent = wheelEvent
        date_edit.installEventFilter(self)
        self._set_compact_date_empty(date_edit)
        return date_edit

    def _set_compact_date_empty(self, date_field):
        empty_date = QDate(1900, 1, 1)
        date_field.setMinimumDate(empty_date)
        date_field.setSpecialValueText("Не указана")
        date_field.setDate(empty_date)

    def _get_compact_date_or_none(self, date_field):
        value = date_field.date()
        if not value.isValid() or value == date_field.minimumDate():
            return None
        return value

    def _get_compact_date_iso_or_none(self, date_field):
        date_value = self._get_compact_date_or_none(date_field)
        return date_value.toString("yyyy-MM-dd") if date_value else None

    def _normalize_compact_year(self, year_value):
        if year_value < 100:
            return 2000 + year_value if year_value <= 49 else 1900 + year_value
        return year_value

    def _build_compact_qdate(self, day, month, year):
        qdate = QDate(self._normalize_compact_year(year), month, day)
        return qdate if qdate.isValid() else None

    def _parse_compact_date_input(self, raw_text):
        text = (raw_text or "").strip()
        if not text:
            return None

        lowered = text.lower().replace("ё", "е")
        if lowered in {"не указана", "не указано", "__.__.____"}:
            return None

        today = QDate.currentDate()
        if lowered in {"сегодня", "today"}:
            return today
        if lowered in {"вчера", "yesterday"}:
            return today.addDays(-1)
        if lowered in {"завтра", "tomorrow"}:
            return today.addDays(1)
        if re.fullmatch(r"[+-]\d+", lowered):
            return today.addDays(int(lowered))

        normalized = lowered.replace("/", ".").replace("-", ".")
        dmy = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", normalized)
        if dmy:
            parsed = self._build_compact_qdate(int(dmy.group(1)), int(dmy.group(2)), int(dmy.group(3)))
            if parsed:
                return parsed

        ymd = re.fullmatch(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", normalized)
        if ymd:
            parsed = self._build_compact_qdate(int(ymd.group(3)), int(ymd.group(2)), int(ymd.group(1)))
            if parsed:
                return parsed

        digits = "".join(ch for ch in lowered if ch.isdigit())
        if len(digits) == 6:
            parsed = self._build_compact_qdate(int(digits[0:2]), int(digits[2:4]), int(digits[4:6]))
            if parsed:
                return parsed
        elif len(digits) == 8:
            parsed = self._build_compact_qdate(int(digits[0:2]), int(digits[2:4]), int(digits[4:8]))
            if parsed:
                return parsed

        return False

    def _set_compact_date_error(self, date_field, message="Некорректная дата"):
        editor = date_field.lineEdit()
        if editor:
            editor.setToolTip(message)
            editor.setStyleSheet("QLineEdit { border: 2px solid #e74c3c; }")

    def _clear_compact_date_error(self, date_field):
        editor = date_field.lineEdit()
        if editor:
            editor.setToolTip("")
            editor.setStyleSheet("")

    def _validate_compact_date_field(self, date_field):
        editor = date_field.lineEdit()
        if not editor:
            return

        parsed = self._parse_compact_date_input(editor.text())
        if parsed is None:
            self._clear_compact_date_error(date_field)
            self._set_compact_date_empty(date_field)
            editor.setInputMask("00.00.0000;_")
            return

        if parsed is False:
            self._set_compact_date_error(date_field, "Некорректная дата")
            return

        self._clear_compact_date_error(date_field)
        date_field.setDate(parsed)
        editor.setInputMask("00.00.0000;_")

    def _editable_date_positions(self):
        return (0, 1, 3, 4, 6, 7, 8, 9)

    def _next_editable_pos(self, pos):
        for p in self._editable_date_positions():
            if p >= pos:
                return p
        return 0

    def _prev_editable_pos(self, pos):
        for p in reversed(self._editable_date_positions()):
            if p <= pos:
                return p
        return 9

    def _normalize_mask_text(self, text):
        chars = list("__.__.____")
        for i, ch in enumerate((text or "")[:10]):
            if i in (2, 5):
                continue
            if ch.isdigit() or ch == "_":
                chars[i] = ch
        return chars

    def _set_mask_editor_text(self, editor, chars, cursor_pos):
        editor.setText("".join(chars))
        editor.setCursorPosition(self._next_editable_pos(cursor_pos))

    def _handle_date_digit_input(self, editor, digit):
        if editor.inputMask() == "":
            editor.setInputMask("00.00.0000;_")
            editor.setText("__.__.____")
            editor.setCursorPosition(0)

        chars = self._normalize_mask_text(editor.text())
        pos = editor.cursorPosition()
        if pos >= 10:
            pos = 0
        if pos in (2, 5):
            pos += 1
        if pos not in self._editable_date_positions():
            pos = self._next_editable_pos(pos)

        chars[pos] = digit
        next_pos = pos + 1
        if next_pos in (2, 5):
            next_pos += 1
        self._set_mask_editor_text(editor, chars, next_pos)

    def _handle_date_delete_input(self, editor, is_backspace=False):
        chars = self._normalize_mask_text(editor.text())
        if editor.hasSelectedText():
            start = editor.selectionStart()
            length = len(editor.selectedText())
            for idx in range(start, start + max(length, 0)):
                if idx in self._editable_date_positions():
                    chars[idx] = "_"
            self._set_mask_editor_text(editor, chars, start if start >= 0 else 0)
            return

        pos = editor.cursorPosition()
        if pos > 9:
            pos = 9
        if is_backspace:
            pos = self._prev_editable_pos(pos - 1 if pos > 0 else 0)
        else:
            if pos in (2, 5):
                pos += 1
            pos = self._next_editable_pos(pos)

        chars[pos] = "_"
        self._set_mask_editor_text(editor, chars, pos)

    def _normalize_date_cursor(self, editor):
        if not isinstance(editor, QLineEdit):
            return
        pos = editor.cursorPosition()
        text_len = len(editor.text())
        if text_len and pos >= text_len - 1:
            editor.setCursorPosition(0)
            return
        if pos == 2:
            editor.setCursorPosition(3)
        elif pos == 5:
            editor.setCursorPosition(6)

    def _handle_compact_date_keypress(self, owner, editor, event):
        key = event.key()
        mods = event.modifiers()
        key_text = event.text()

        if key_text and (key_text.isalpha() or key_text in "+- "):
            if editor.inputMask():
                previous = editor.text()
                editor.setInputMask("")
                if not any(ch.isdigit() for ch in previous):
                    editor.clear()
            return False

        if (mods & Qt.ControlModifier) and key in (Qt.Key_Backspace, Qt.Key_Delete):
            self._set_compact_date_empty(owner)
            return True

        if (mods & Qt.ControlModifier) and key == Qt.Key_V:
            self._paste_compact_date_from_clipboard(owner, editor)
            return True

        if key_text and key_text.isdigit():
            self._handle_date_digit_input(editor, key_text)
            return True

        if key in (Qt.Key_Period, Qt.Key_Slash, Qt.Key_Minus, Qt.Key_Comma):
            pos = editor.cursorPosition()
            if pos <= 1:
                editor.setCursorPosition(3)
            elif pos <= 4:
                editor.setCursorPosition(6)
            else:
                editor.setCursorPosition(0)
            return True

        if key == Qt.Key_Backspace:
            self._handle_date_delete_input(editor, is_backspace=True)
            return True
        if key == Qt.Key_Delete:
            self._handle_date_delete_input(editor, is_backspace=False)
            return True
        if key == Qt.Key_Home:
            editor.setCursorPosition(0)
            return True
        if key == Qt.Key_End:
            editor.setCursorPosition(0)
            return True
        return False

    def _paste_compact_date_from_clipboard(self, owner, editor):
        paste_text = QApplication.clipboard().text()
        parsed = self._parse_compact_date_input(paste_text)
        if parsed is None:
            self._set_compact_date_empty(owner)
            self._clear_compact_date_error(owner)
        elif parsed is False:
            editor.setInputMask("")
            editor.setText(paste_text)
            self._set_compact_date_error(owner, "Некорректная дата")
        else:
            owner.setDate(parsed)
            self._clear_compact_date_error(owner)

    def _show_compact_date_context_menu(self, owner, editor, global_pos):
        menu = QMenu(self)

        cut_action = QAction("Вырезать", self)
        cut_action.triggered.connect(editor.cut)
        copy_action = QAction("Копировать", self)
        copy_action.triggered.connect(editor.copy)
        paste_action = QAction("Вставить", self)
        paste_action.triggered.connect(lambda: self._paste_compact_date_from_clipboard(owner, editor))
        select_all_action = QAction("Выделить всё", self)
        select_all_action.triggered.connect(editor.selectAll)

        clear_value_action = QAction("Очистить выделение", self)
        clear_value_action.triggered.connect(lambda: self._handle_date_delete_input(editor, is_backspace=False))

        clear_date_action = QAction("Очистить дату", self)
        clear_date_action.triggered.connect(lambda: self._set_compact_date_empty(owner))

        today_action = QAction("Сегодня", self)
        today_action.triggered.connect(lambda: owner.setDate(QDate.currentDate()))

        menu.addAction(cut_action)
        menu.addAction(copy_action)
        menu.addAction(paste_action)
        menu.addAction(select_all_action)
        menu.addSeparator()
        menu.addAction(clear_value_action)
        menu.addAction(clear_date_action)
        menu.addSeparator()
        menu.addAction(today_action)

        cut_action.setEnabled(editor.hasSelectedText())
        copy_action.setEnabled(editor.hasSelectedText())
        clear_value_action.setEnabled(editor.hasSelectedText())

        menu.exec_(global_pos)

    def eventFilter(self, obj, event):
        if isinstance(obj, QDateEdit):
            if event.type() == QEvent.MouseButtonPress:
                if self._get_compact_date_or_none(obj) is None:
                    obj.setDate(QDate.currentDate())
                return False

            if event.type() == QEvent.KeyPress:
                editor = obj.lineEdit()
                if editor and self._handle_compact_date_keypress(obj, editor, event):
                    return True

                modifiers = event.modifiers()
                if event.key() in (Qt.Key_Up, Qt.Key_Down):
                    base_date = self._get_compact_date_or_none(obj) or QDate.currentDate()
                    delta = 1 if event.key() == Qt.Key_Up else -1
                    if modifiers & Qt.ShiftModifier:
                        obj.setDate(base_date.addYears(delta))
                    elif modifiers & Qt.ControlModifier:
                        obj.setDate(base_date.addMonths(delta))
                    else:
                        obj.setDate(base_date.addDays(delta))
                    return True

        if isinstance(obj, QLineEdit):
            owner = obj.property("_compact_date_owner")
            if isinstance(owner, QDateEdit) and event.type() == QEvent.MouseButtonPress:
                if self._get_compact_date_or_none(owner) is None:
                    owner.setDate(QDate.currentDate())
                QTimer.singleShot(0, lambda e=obj: self._normalize_date_cursor(e))
                return False

            if isinstance(owner, QDateEdit) and event.type() == QEvent.ContextMenu:
                self._show_compact_date_context_menu(owner, obj, event.globalPos())
                return True

            if isinstance(owner, QDateEdit) and event.type() == QEvent.KeyPress:
                if self._handle_compact_date_keypress(owner, obj, event):
                    return True

        return super().eventFilter(obj, event)
    
    def get_button_style(self, color, hover_color):
        """Получить унифицированный стиль кнопки"""
        if color == "#28a745":  # Success
            return AppStyles.button_success()
        elif color == "#ffc107":  # Warning
            return AppStyles.button_warning()
        else:
            return AppStyles.button_primary()
    
    def apply_modern_styles(self):
        """Применить унифицированные стили"""
        self.setStyleSheet(f"""
            QWidget {{
                background-color: white;
                color: {AppColors.TEXT_PRIMARY};
                font-family: 'Segoe UI', Arial, sans-serif;
            }}
            
            QLabel {{
                font-size: 10pt;
                color: {AppColors.TEXT_PRIMARY};
            }}
            
            {AppStyles.input_field()}
            {AppStyles.scroll_bar()}
        """)
    
    def load_references(self):
        """Загрузить все справочники"""
        print("📚 Загрузка справочников...")
        self.load_statuses()
        self.load_document_types()
        self.load_document_kinds()
        self.load_signing_types()
        self.load_executors()
        self.load_themes()
        print("✅ Все справочники загружены")
    def load_executors(self):
        """Загрузить исполнителей"""
        try:
            self.executor_field.clear()
            self.executor_field.addItem("-- Не выбран --", None)
            
            executors = self.db_manager.get_executors(active_only=True)
            for executor in executors:
                name = executor.get('name', '')
                position = executor.get('position', '')
                display_text = f"{name} ({position})" if position else name
                self.executor_field.addItem(display_text, executor.get('id'))
                
        except Exception as e:
            print(f"❌ Ошибка загрузки исполнителей: {e}")
    
    def load_themes(self):
        """Загрузить темы"""
        try:
            self.theme_field.clear()
            self.theme_field.addItem("-- Не выбрана --", None)
            
            themes = self.db_manager.get_themes(active_only=True)
            for theme in themes:
                self.theme_field.addItem(theme.get('name', ''), theme.get('id'))
                
        except Exception as e:
            print(f"❌ Ошибка загрузки тем: {e}")
    
    def connect_change_signals(self):
        """Подключить сигналы изменения полей"""
        try:
            fields = [
                self.status_field, self.type_field, self.document_kind_field, 
                self.signing_type_field, self.executor_field, self.theme_field
            ]
            
            for field in fields:
                if isinstance(field, QComboBox):
                    field.currentTextChanged.connect(self.on_field_changed)
            
            line_edits = [self.reg_number_field, self.number_field, self.title_field]
            for field in line_edits:
                field.textChanged.connect(self.on_field_changed)
            
            self.reg_date_field.dateChanged.connect(self.on_field_changed)
            
            # Количества
            self.pages_count_field.valueChanged.connect(self.on_field_changed)
            self.attachments_count_field.valueChanged.connect(self.on_field_changed)
                
        except Exception as e:
            print(f"❌ Ошибка подключения сигналов: {e}")
    def load_statuses(self):
        """Загрузить статусы из БД"""
        try:
            self.status_field.clear()
            self.status_field.addItem("-- Не выбран --", None)

            statuses = self.db_manager.get_simple_reference("ref_status")
            for status in statuses:
                self.status_field.addItem(status.get("name", ""), status.get("id"))

            print(f"✅ Загружено статусов: {len(statuses)}")
                
        except Exception as e:
            print(f"❌ Ошибка загрузки статусов: {e}")


    def load_document_types(self):
        """Загрузить типы документов из БД"""
        try:
            self.type_field.clear()
            self.type_field.addItem("-- Не выбран --", None)

            types = self.db_manager.get_simple_reference("ref_document_types")
            for doc_type in types:
                self.type_field.addItem(doc_type.get("name", ""), doc_type.get("id"))

            print(f"✅ Загружено типов документов: {len(types)}")
                
        except Exception as e:
            print(f"❌ Ошибка загрузки типов документов: {e}")


    def load_document_kinds(self):
        """Загрузить виды документов из БД"""
        try:
            self.document_kind_field.clear()
            self.document_kind_field.addItem("-- Не выбран --", None)

            kinds = self.db_manager.get_simple_reference("ref_document_kinds")
            for kind in kinds:
                self.document_kind_field.addItem(kind.get("name", ""), kind.get("id"))

            print(f"✅ Загружено видов документов: {len(kinds)}")
                
        except Exception as e:
            print(f"❌ Ошибка загрузки видов документов: {e}")


    def load_signing_types(self):
        """Загрузить типы подписания из БД"""
        try:
            self.signing_type_field.clear()
            self.signing_type_field.addItem("-- Не выбран --", None)

            types = self.db_manager.get_simple_reference("ref_signing_types")
            for signing_type in types:
                self.signing_type_field.addItem(signing_type.get("name", ""), signing_type.get("id"))

            print(f"✅ Загружено типов подписания: {len(types)}")
                
        except Exception as e:
            print(f"❌ Ошибка загрузки типов подписания: {e}")
    def disconnect_change_signals(self):
        """Отключить сигналы изменения полей"""
        try:
            # ComboBox'ы
            fields = [
                self.status_field, self.type_field, self.document_kind_field,
                self.signing_type_field, self.executor_field, self.theme_field
            ]
            
            for field in fields:
                if isinstance(field, QComboBox):
                    try:
                        field.currentTextChanged.disconnect(self.on_field_changed)
                    except TypeError:
                        pass  # Сигнал не был подключен
            
            # LineEdit'ы
            line_edits = [self.reg_number_field, self.number_field, self.title_field]
            for field in line_edits:
                try:
                    field.textChanged.disconnect(self.on_field_changed)
                except TypeError:
                    pass
            
            # DateEdit
            try:
                self.reg_date_field.dateChanged.disconnect(self.on_field_changed)
            except TypeError:
                pass
            
            # SpinBox'ы
            try:
                self.pages_count_field.valueChanged.disconnect(self.on_field_changed)
            except TypeError:
                pass
            
            try:
                self.attachments_count_field.valueChanged.disconnect(self.on_field_changed)
            except TypeError:
                pass
                
        except Exception as e:
            print(f"⚠️ Предупреждение при отключении сигналов: {e}")
    
    def on_field_changed(self):
        """Обработчик изменения любого поля"""
        if self.current_document_id:
            self.save_btn.setEnabled(True)
            self.reset_btn.setEnabled(True)
            self.status_bar.setText("Есть несохраненные изменения")
            self.status_bar.setStyleSheet("QLabel { color: #dc3545; font-weight: bold; }")
    
    def set_document(self, document_data):
        """Установить данные документа"""
        try:
            # ⭐⭐⭐ КРИТИЧЕСКИ ВАЖНО: Добавьте эти строки В САМОЕ НАЧАЛО!
            print(f"\n{'='*60}")
            print(f"🎯 CompactMetadataEditor.set_document() ВЫЗВАН!")
            print(f"   Тип данных: {type(document_data)}")
            
            if document_data:
                print(f"   ✅ Данные получены:")
                print(f"      ID: {document_data.get('id', 'НЕТ')}")
                print(f"      Название: {document_data.get('title', 'НЕТ')[:50]}")
                print(f"      Статус: {document_data.get('status', 'НЕТ')}")
            else:
                print("   INFO: document_data пустой, документ мог быть удален в другой сессии")
                print(f"{'='*60}\n")
            
            # СУЩЕСТВУЮЩИЙ КОД начинается здесь:
            if not document_data:
                print("   INFO: очищаем форму, потому что документ больше не найден")
                self.clear_form()
                return
            
            self.current_document_id = document_data.get("id")
            print(f"   📝 Установлен current_document_id = {self.current_document_id}")
            
            self.disconnect_change_signals()
            print(f"   🔇 Отключены сигналы изменения")
            
            # Обновляем заголовок
            title = document_data.get("title", "Без названия")
            if len(title) > 40:
                self.title_display.setText(f"📄 {title[:37]}...")
                self.title_display.setToolTip(title)
            else:
                self.title_display.setText(f"📄 {title}")
                self.title_display.setToolTip("")
            
            print(f"   ✅ Заголовок установлен: {title[:30]}...")
            
            # Загружаем данные в поля через ID
            print(f"   📋 Загружаем данные в поля...")
            print(f"      status_id: {document_data.get('status_id')}")
            print(f"      type_id: {document_data.get('type_id')}")
            print(f"      document_kind_id: {document_data.get('document_kind_id')}")
            print(f"      signing_type_id: {document_data.get('signing_type_id')}")

            # Устанавливаем значения через ID
            self.set_combo_by_id(self.status_field, document_data.get("status_id"))
            self.set_combo_by_id(self.type_field, document_data.get("type_id"))
            self.set_combo_by_id(self.document_kind_field, document_data.get("document_kind_id"))
            self.set_combo_by_id(self.signing_type_field, document_data.get("signing_type_id"))
            
            self.reg_number_field.setText(document_data.get("reg_number", ""))
            self.number_field.setText(document_data.get("number", ""))
            self.title_field.setText(document_data.get("title", ""))
            
            # Даты
            reg_date = document_data.get("reg_date")
            if reg_date:
                try:
                    parsed_date = parse_flexible_datetime(reg_date)
                    self.reg_date_field.setDate(QDate(parsed_date.year, parsed_date.month, parsed_date.day))
                except Exception:
                    self._set_compact_date_empty(self.reg_date_field)
            else:
                self._set_compact_date_empty(self.reg_date_field)
            
            # Справочники
            self.set_combo_by_id(self.executor_field, document_data.get("executor_id"))
            self.set_combo_by_id(self.theme_field, document_data.get("theme_id"))
            
            # Количества
            self.pages_count_field.setValue(document_data.get("pages_count", 0) or 0)
            self.attachments_count_field.setValue(document_data.get("attachments_count", 0) or 0)
            
            # Обновляем UI
            self.save_btn.setEnabled(False)
            self.reset_btn.setEnabled(True)
            self.status_bar.setText("Данные загружены")
            self.status_bar.setStyleSheet("QLabel { color: #28a745; }")
            # Активируем кнопки предпросмотра и открытия
            if self.current_document_id:
                self.quick_preview_btn.setEnabled(True)
                self.open_file_btn.setEnabled(True)
                try:
                    if hasattr(self.db_manager, "get_document_edit_snapshot"):
                        self._loaded_snapshot = self.db_manager.get_document_edit_snapshot(self.current_document_id)
                    else:
                        self._loaded_snapshot = None
                except Exception:
                    self._loaded_snapshot = None
        
        
            self.connect_change_signals()
            
            
        except Exception as e:
            print(f"❌ Ошибка загрузки метаданных: {e}")
            import traceback
            traceback.print_exc()
    def open_quick_preview(self):
        """Открыть быстрый предпросмотр"""
        if not self.current_document_id:
            return
        
        try:
            main_window = self.get_main_window()
            if main_window:
                main_window.open_document_preview_by_id(self.current_document_id)
        except Exception as e:
            print(f"❌ Ошибка открытия предпросмотра: {e}")


    def open_in_word(self):
        """Открыть файл в Word/LibreOffice"""
        if not self.current_document_id:
            return
        
        try:
            main_window = self.get_main_window()
            if main_window:
                main_window.open_file_by_document_id(self.current_document_id)
        except Exception as e:
            print(f"❌ Ошибка открытия файла: {e}")


    def get_main_window(self):
        """Получить главное окно"""
        widget = self
        while widget is not None:
            if widget.__class__.__name__ == 'MainWindow':
                return widget
            widget = widget.parent()
        return None

    def set_combo_by_id(self, combo, target_id):
        """Установить значение комбобокса по ID"""
        if target_id:
            for i in range(combo.count()):
                if combo.itemData(i) == target_id:
                    combo.setCurrentIndex(i)
                    return
        combo.setCurrentIndex(0)
    
    def clear_form(self):
        """Очистить форму"""
        self.current_document_id = None
        self._loaded_snapshot = None
        self.title_display.setText("📄 Документ не выбран")

        # Сброс всех полей
        for combo in [self.status_field, self.type_field, self.document_kind_field,
                     self.signing_type_field, self.executor_field, self.theme_field]:
            combo.setCurrentIndex(0)
        
        for edit in [self.reg_number_field, self.number_field, self.title_field]:
            edit.clear()
        
        self._set_compact_date_empty(self.reg_date_field)
        self.pages_count_field.setValue(0)
        self.attachments_count_field.setValue(0)
        
        self.save_btn.setEnabled(False)
        self.reset_btn.setEnabled(False)
        self.status_bar.setText("Готов к работе")
        self.status_bar.setStyleSheet("QLabel { color: #6c757d; }")
        if hasattr(self, 'quick_preview_btn'):
            self.quick_preview_btn.setEnabled(False)
        if hasattr(self, 'open_file_btn'):
            self.open_file_btn.setEnabled(False)
    def save_metadata(self):
        """Сохранить изменения метаданных"""
        if not self.current_document_id:
            return
        
        try:
            print(f"\n{'='*60}")
            print(f"💾 СОХРАНЕНИЕ МЕТАДАННЫХ документа ID: {self.current_document_id}")
            
            # Получаем ID из комбобоксов (они хранятся в itemData)
            status_id = self.status_field.currentData()
            type_id = self.type_field.currentData()
            kind_id = self.document_kind_field.currentData()
            signing_id = self.signing_type_field.currentData()
            executor_id = self.executor_field.currentData()
            theme_id = self.theme_field.currentData()
            
            # Выводим для отладки
            print(f"   status_id: {status_id} ({self.status_field.currentText()})")
            print(f"   type_id: {type_id} ({self.type_field.currentText()})")
            print(f"   kind_id: {kind_id} ({self.document_kind_field.currentText()})")
            print(f"   signing_id: {signing_id} ({self.signing_type_field.currentText()})")
            print(f"   executor_id: {executor_id}")
            print(f"   theme_id: {theme_id}")
            
            # Формируем данные для обновления (используем правильные имена полей БД)
            updated_data = {
                "status_id": status_id,
                "type_id": type_id,
                "document_kind_id": kind_id,
                "signing_type_id": signing_id,
                "reg_number": self.reg_number_field.text().strip(),
                "number": self.number_field.text().strip(),
                "reg_date": self._get_compact_date_iso_or_none(self.reg_date_field),
                "executor_id": executor_id,
                "theme_id": theme_id,
                "title": self.title_field.text().strip(),
                "pages_count": self.pages_count_field.value(),
                "attachments_count": self.attachments_count_field.value(),
            }
            
            print(f"   📦 Данные для сохранения: {updated_data}")
            
            # Сохраняем в БД с проверкой конфликтов параллельного редактирования.
            try:
                self.db_manager.update_document(
                    self.current_document_id,
                    updated_data,
                    expected_snapshot=self._loaded_snapshot,
                    force_overwrite=False
                )
            except TypeError:
                # Совместимость с менеджером БД без conflict-check API.
                self.db_manager.update_document(self.current_document_id, updated_data)
            except DocumentConflictError:
                reply = QMessageBox.question(
                    self,
                    "Конфликт изменений",
                    "Документ уже изменён другим пользователем.\n\n"
                    "Нажмите 'Да', чтобы перезаписать его вашими данными.\n"
                    "Нажмите 'Нет', чтобы перезагрузить актуальную версию.",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )
                if reply == QMessageBox.Yes:
                    self.db_manager.update_document(
                        self.current_document_id,
                        updated_data,
                        expected_snapshot=None,
                        force_overwrite=True
                    )
                else:
                    document_data = self.db_manager.get_document_by_id(self.current_document_id)
                    if document_data:
                        self.set_document(document_data)
                    return

            if hasattr(self.db_manager, "get_document_edit_snapshot"):
                try:
                    self._loaded_snapshot = self.db_manager.get_document_edit_snapshot(self.current_document_id)
                except Exception:
                    self._loaded_snapshot = None
            
            print(f"✅ Метаданные сохранены успешно!")
            print(f"{'='*60}\n")
            
            # Обновляем UI
            self.save_btn.setEnabled(False)
            self.reset_btn.setEnabled(True)
            self.status_bar.setText("✅ Сохранено")
            self.status_bar.setStyleSheet("QLabel { color: #28a745; font-weight: bold; }")
            
            # Отправляем сигнал для обновления таблиц
            self.metadata_saved.emit(self.current_document_id)
            
            # Таймер для сброса статуса
            QTimer.singleShot(3000, lambda: (
                self.status_bar.setText("Готов к работе"),
                self.status_bar.setStyleSheet("QLabel { color: #6c757d; }")
            ))
            
        except Exception as e:
            print(f"❌ ОШИБКА СОХРАНЕНИЯ: {e}")
            import traceback
            traceback.print_exc()
            print(f"{'='*60}\n")
            
            self.status_bar.setText("❌ Ошибка сохранения")
            self.status_bar.setStyleSheet("QLabel { color: #dc3545; font-weight: bold; }")
    def get_reference_id(self, table_name, name):
        """
        Получить ID элемента справочника по имени
        
        Args:
            table_name: Имя таблицы справочника
            name: Название элемента
        
        Returns:
            int или None: ID элемента или None если не найден
        """
        try:
            if hasattr(self.db_manager, "get_reference_id_by_name"):
                return self.db_manager.get_reference_id_by_name(table_name, name)

            result = self.db_manager.execute_query(
                f"SELECT id FROM {table_name} WHERE name = ?",
                (name,),
            )
            return result[0].get("id") if result else None
        except Exception as e:
            print(f"❌ Ошибка получения ID из {table_name}: {e}")
            return None
    def reset_form(self):
        """Сбросить форму к исходным значениям"""
        if self.current_document_id:
            try:
                document_data = self.db_manager.get_document_by_id(self.current_document_id)
                if document_data:
                    self.set_document(document_data)
            except Exception as e:
                print(f"❌ Ошибка сброса формы: {e}")
    
    def set_document_data(self, document_data):
        """Установить данные документа (альтернативное название для совместимости)"""
        self.set_document(document_data)
    
    def refresh_references(self):
        """Обновить справочники"""
        self.load_references()
    def reload_references(self):
        """
        ⭐ Перезагрузить все справочники после изменений
        
        Вызывается когда пользователь изменил справочники
        """
        try:
            print("🔄 CompactMetadataEditor: Перезагрузка справочников...")
            
            # Сохраняем текущие выбранные ID
            current_values = {}
            if self.current_document_id:
                current_values = {
                    'status_id': self.status_field.currentData(),
                    'type_id': self.type_field.currentData(),
                    'document_kind_id': self.document_kind_field.currentData(),
                    'signing_type_id': self.signing_type_field.currentData(),
                    'executor_id': self.executor_field.currentData(),
                    'theme_id': self.theme_field.currentData(),
                }
                print(f"   💾 Сохранены текущие значения: {current_values}")
            
            # Отключаем сигналы на время перезагрузки
            self.disconnect_change_signals()
            
            # Перезагружаем все справочники
            print(f"   ↻ Перезагрузка статусов...")
            self.load_statuses()
            
            print(f"   ↻ Перезагрузка типов документов...")
            self.load_document_types()
            
            print(f"   ↻ Перезагрузка видов документов...")
            self.load_document_kinds()
            
            print(f"   ↻ Перезагрузка типов подписания...")
            self.load_signing_types()
            
            print(f"   ↻ Перезагрузка исполнителей...")
            self.load_executors()
            
            print(f"   ↻ Перезагрузка тем...")
            self.load_themes()
            
            # Восстанавливаем выбранные значения
            if self.current_document_id and current_values:
                print(f"   🔄 Восстановление выбранных значений...")
                
                self.set_combo_by_id(self.status_field, current_values.get('status_id'))
                self.set_combo_by_id(self.type_field, current_values.get('type_id'))
                self.set_combo_by_id(self.document_kind_field, current_values.get('document_kind_id'))
                self.set_combo_by_id(self.signing_type_field, current_values.get('signing_type_id'))
                self.set_combo_by_id(self.executor_field, current_values.get('executor_id'))
                self.set_combo_by_id(self.theme_field, current_values.get('theme_id'))
                
                print(f"   ✅ Значения восстановлены")
            
            # Включаем сигналы обратно
            self.connect_change_signals()
            
            print("✅ CompactMetadataEditor: Все справочники перезагружены успешно!")
            
        except Exception as e:
            print(f"❌ Ошибка перезагрузки справочников в CompactMetadataEditor: {e}")
            import traceback
            traceback.print_exc()
