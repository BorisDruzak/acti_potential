import logging
import os
import sys
from datetime import datetime

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QMessageBox

import app_config
from app_diagnostics import setup_application_logging
from database_backends import DatabaseSwitcher
from database_switcher_dialog import DatabaseSwitcherDialog
from gui import MainWindow
from work_with_word_docs import WordDocumentHandler


def initialize_data_structure() -> str:
    data_folder = app_config.get_data_path()
    os.makedirs(data_folder, exist_ok=True)
    return data_folder


def get_database_manager(data_folder: str):
    switcher = DatabaseSwitcher()

    if switcher.current_manager and switcher.current_db:
        return switcher.current_manager, switcher

    return None, switcher


def request_database_connection(switcher: DatabaseSwitcher) -> tuple[object, DatabaseSwitcher]:
    if switcher.has_profiles():
        QMessageBox.warning(
            None,
            "База данных не подключена",
            "Не удалось автоматически подключиться ни к одному доступному профилю базы данных.\n\n"
            "Проверьте параметры подключения или выберите рабочий профиль вручную."
        )
    else:
        QMessageBox.warning(
            None,
            "База данных не настроена",
            "В программе пока не настроено ни одного профиля базы данных.\n\n"
            "Сейчас откроется окно управления профилями, где можно добавить подключение к SQL Server "
            "или локальную SQLite базу."
        )

    dialog = DatabaseSwitcherDialog()
    dialog.exec_()

    refreshed_switcher = DatabaseSwitcher()
    if refreshed_switcher.current_manager and refreshed_switcher.current_db:
        return refreshed_switcher.current_manager, refreshed_switcher

    raise RuntimeError(
        "База данных не подключена. Выберите или настройте рабочий профиль подключения."
    )


def main():
    app_root = app_config.get_app_root()
    setup_application_logging(app_root)
    logger = logging.getLogger("acti.main")

    try:
        app = QApplication(sys.argv)
        if getattr(sys, "frozen", False):
            icon_path = os.path.join(app_root, "app_icon.ico")
            if os.path.exists(icon_path):
                app.setWindowIcon(QIcon(icon_path))

        data_folder = initialize_data_structure()
        db_manager, switcher = get_database_manager(data_folder)
        if db_manager is None:
            db_manager, switcher = request_database_connection(switcher)

        document_handler = WordDocumentHandler()
        window = MainWindow(db_manager, document_handler, switcher)
        window.showMaximized()
        logger.info("Приложение запущено")
        sys.exit(app.exec_())
    except Exception as exc:
        error_message = f"Критическая ошибка запуска:\n\n{exc}"
        logging.getLogger("acti.startup").exception(error_message)
        try:
            QMessageBox.critical(None, "Ошибка", error_message)
        except Exception:
            print(error_message)

        import traceback

        log_path = os.path.join(app_root, "error.log")
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write(f"Date: {datetime.now()}\n")
            handle.write(error_message + "\n\n")
            handle.write(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
