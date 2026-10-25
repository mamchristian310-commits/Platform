import sys

from PySide6.QtWidgets import QApplication, QMessageBox

import models
from main_window import MainWindow, application_icon


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")   # the same look on every computer
    app.setWindowIcon(application_icon())

    try:
        models.init_db()     # creates the database file the first time
    except Exception as error:
        QMessageBox.critical(
            None,
            "Cannot open the database",
            f"The records file could not be opened or created:\n\n{models.DB_PATH}\n\n{error}",
        )
        sys.exit(1)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
