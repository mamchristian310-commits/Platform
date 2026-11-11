"""
main_window.py -- the window and its three tabs: Today, History, Employees.

The window never touches the database. It asks services.py to do the work
and reports.py to make Excel files, then shows the answers.
"""

import os
import sys
from datetime import date

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import models
import reports
import services
from common import RuleError, fold, format_duration, format_time

ALL_DEPARTMENTS = "All structures"

GREEN = QBrush(QColor("#d8f3dc"))   # background of people still on the platform
RED = QBrush(QColor("#f8d7da"))
BLACK = QBrush(QColor("black"))
GREY = QBrush(QColor("gray"))

BIG_BOLD = "font-size: 15px; font-weight: bold;"


def application_icon():
    resource_folder = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return QIcon(os.path.join(resource_folder, "platform_icon.ico"))


# ---------------------------------------------------------------
# SMALL HELPERS FOR TABLES
# ---------------------------------------------------------------

def make_table(headers, widths):
    table = QTableWidget()
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    for column, width in enumerate(widths):
        table.setColumnWidth(column, width)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)           # read-only
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)    # click = whole row
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def make_searchable_combo(combo, placeholder):
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    combo.lineEdit().setPlaceholderText(placeholder)
    combo.lineEdit().setClearButtonEnabled(True)
    completer = combo.completer()
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    completer.setFilterMode(Qt.MatchFlag.MatchContains)
    completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)


def add_row(table, values, record_id=None, highlight=False, overdue=False):
    row = table.rowCount()
    table.insertRow(row)
    for column, value in enumerate(values):
        item = QTableWidgetItem(str(value))
        if column == 0 and record_id is not None:
            # Hide the record's id inside the first cell, so we can find
            # the record again when the row is clicked
            item.setData(Qt.ItemDataRole.UserRole, record_id)
        if highlight or overdue:
            item.setBackground(RED if overdue else GREEN)
            item.setForeground(BLACK)
        table.setItem(row, column, item)


def selected_record_id(table):
    items = table.selectedItems()
    if not items:
        return None
    first_cell = table.item(items[0].row(), 0)
    return first_cell.data(Qt.ItemDataRole.UserRole)


def history_values(record):
    visit_date = record.time_in.date()
    return [
        visit_date.strftime("%Y-%m-%d"),
        record.employee,
        record.department,
        record.badge,
        record.phone_number or "Not set",
        str(record.id_card_number) if record.id_card_number is not None else "Not set",
        format_time(record.time_in, visit_date),
        format_time(record.time_out, visit_date),
        format_duration(record.duration),
    ]


# ---------------------------------------------------------------
# THE WINDOW
# "self" means "this window": everything it needs to remember
# (its boxes, tables and lists) is stored on self.
# ---------------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Platform Attendance Log")
        self.setWindowIcon(application_icon())
        self.resize(1100, 680)

        self.today_records = {}   # record id -> Record, for the Today table
        self.last_search = []     # the records currently shown on the History tab

        self.build_menu()

        tabs = QTabWidget()
        tabs.addTab(self.build_today_tab(), "Today")
        tabs.addTab(self.build_history_tab(), "History")
        tabs.addTab(self.build_employees_tab(), "Employees")
        self.setCentralWidget(tabs)

        self.statusBar().showMessage("Ready.")

        self.refresh_all()
        self.search_clicked()   # show today's records on the History tab

        # If the program stays open all night, this keeps "Today" correct
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_today)
        self.timer.start(60_000)   # every 60 seconds

    # ----- messages and file dialogs (all in one place) -----
    def warn(self, title, text):
        QMessageBox.warning(self, title, text)

    def error(self, title, text):
        QMessageBox.critical(self, title, text)

    def info(self, title, text):
        QMessageBox.information(self, title, text)

    def confirm(self, title, text):
        answer = QMessageBox.question(
            self,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def ask_save_path(self, title, default_name, file_filter):
        path, selected_filter = QFileDialog.getSaveFileName(self, title, default_name, file_filter)
        return path

    # ----- menu -----
    def build_menu(self):
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        backup_action = QAction("Backup database...", self)
        backup_action.triggered.connect(self.backup_clicked)
        file_menu.addAction(backup_action)
        file_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        help_menu = menu_bar.addMenu("&Help")
        data_action = QAction("Where is my data?", self)
        data_action.triggered.connect(self.show_data_location)
        help_menu.addAction(data_action)

    def show_data_location(self):
        self.info(
            "Where is my data?",
            "All records are saved, instantly, in this file on this computer:\n\n"
            f"{models.DB_PATH}\n\n"
            "No internet is used. Make regular copies with File > Backup database.",
        )

    def backup_clicked(self):
        default_name = f"platform_log_backup_{date.today():%Y-%m-%d}.db"
        path = self.ask_save_path("Backup database", default_name, "Database file (*.db)")
        if not path:
            return
        try:
            services.backup_database(path)
        except (RuleError, OSError) as error:
            self.error("Backup failed", str(error))
            return
        self.statusBar().showMessage(f"Backup saved: {path}")
        self.info("Backup saved", f"A copy of all records was saved to:\n\n{path}")

    # =============================================================
    # TAB 1: TODAY  (the screen used all day long)
    # =============================================================
    def build_today_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        form = QGroupBox("Check in an employee  (give them their badge, keep their ID card)")
        form_layout = QHBoxLayout(form)

        self.checkin_department = QComboBox()
        self.checkin_employee = QComboBox()
        make_searchable_combo(self.checkin_department, "Type or choose a structure")
        make_searchable_combo(self.checkin_employee, "Type or choose an employee")
        self.badge_label = QLabel("Badge: -")
        self.badge_label.setStyleSheet(BIG_BOLD)
        checkin_button = QPushButton("Check in")

        form_layout.addWidget(QLabel("Structure"))
        form_layout.addWidget(self.checkin_department, 1)
        form_layout.addWidget(QLabel("Employee"))
        form_layout.addWidget(self.checkin_employee, 2)
        form_layout.addWidget(self.badge_label)
        form_layout.addWidget(checkin_button)
        layout.addWidget(form)

        # Choosing a structure fills the employee list;
        # choosing an employee shows THEIR badge
        self.checkin_department.currentIndexChanged.connect(self.refresh_checkin_employees)
        self.checkin_employee.currentIndexChanged.connect(self.update_badge_label)
        checkin_button.clicked.connect(self.check_in_clicked)

        self.inside_label = QLabel("")
        self.inside_label.setStyleSheet(BIG_BOLD)
        layout.addWidget(self.inside_label)

        department_filter = QHBoxLayout()
        department_filter.addWidget(QLabel("Structure"))
        self.today_department_filter = QComboBox()
        department_filter.addWidget(self.today_department_filter, 1)
        self.today_department_filter.currentIndexChanged.connect(self.refresh_today)
        department_filter.addStretch(1)
        layout.addLayout(department_filter)

        self.today_search = QLineEdit()
        self.today_search.setPlaceholderText("Find employee by name, structure, or badge")
        self.today_search.setClearButtonEnabled(True)
        self.today_search.textChanged.connect(self.refresh_today)
        layout.addWidget(self.today_search)

        self.today_table = make_table(
            ["Employee", "Structure", "Badge", "Phone number", "ID card number", "Time in", "Time out"],
            [190, 160, 80, 140, 130, 130, 130],
        )
        layout.addWidget(self.today_table)

        checkout_button = QPushButton("Check out selected employee  (badge returned, ID card given back)")
        checkout_button.clicked.connect(self.check_out_clicked)
        layout.addWidget(checkout_button)
        return tab

    def update_badge_label(self):
        data = self.checkin_employee.currentData()
        if data is None:
            self.badge_label.setText("Badge: -")
        else:
            employee_id, badge = data
            self.badge_label.setText(f"Badge: {badge}")

    def check_in_clicked(self):
        data = self.checkin_employee.currentData()
        if data is None:
            self.warn("Missing information", "Choose a structure and an employee.")
            return
        employee_id, badge = data

        try:
            name, badge, time_in = services.check_in(employee_id)
        except RuleError as error:
            self.error("Cannot check in", str(error))
            return

        self.statusBar().showMessage(
            f"{name} checked in at {time_in:%H:%M}. Keep the ID card, give badge {badge}."
        )
        self.refresh_today()
        self.refresh_checkin_employees()   # this person can't check in again now
        self.refresh_employees()

    def check_out_clicked(self):
        record_id = selected_record_id(self.today_table)
        if record_id is None:
            self.warn("Nothing selected", "Click an employee in the table first.")
            return

        record = self.today_records.get(record_id)
        if record is None:
            self.refresh_today()
            return
        if record.time_out is not None:
            self.info("Already checked out", f"{record.employee} has already left.")
            return

        # A check-out can't be undone, so ask first
        if not self.confirm(
            "Check out",
            f"Check out {record.employee}?\n\nTake back badge {record.badge} and give back the ID card.",
        ):
            return

        try:
            name, badge, time_out = services.check_out(record_id)
        except RuleError as error:
            self.error("Cannot check out", str(error))
            return

        self.statusBar().showMessage(
            f"{name} checked out at {time_out:%H:%M}. Badge {badge} taken back, ID card returned."
        )
        self.refresh_today()
        self.refresh_checkin_employees()
        self.refresh_employees()

    def refresh_today(self):
        selected_id = selected_record_id(self.today_table)   # remember the selected row
        records = services.get_today_records()
        today = date.today()

        self.today_records = {record.id: record for record in records}
        self.today_table.setRowCount(0)
        inside = 0
        search_text = fold(self.today_search.text().strip())
        department_id = self.today_department_filter.currentData()
        department_names = dict(services.list_departments())

        for record in records:
            still_inside = record.time_out is None
            if still_inside:
                inside += 1
            if department_id is not None and department_names.get(department_id) != record.department:
                continue
            if search_text and not any(
                search_text in fold(value)
                for value in (
                    record.employee,
                    record.department,
                    record.badge,
                    record.phone_number or "",
                    str(record.id_card_number or ""),
                )
            ):
                continue
            overdue = record.time_in.date() < today and (
                record.time_out is None or record.time_out.date() > record.time_in.date()
            )
            add_row(
                self.today_table,
                [
                    record.employee,
                    record.department,
                    record.badge,
                    record.phone_number or "Not set",
                    str(record.id_card_number) if record.id_card_number is not None else "Not set",
                    format_time(record.time_in, today),
                    format_time(record.time_out, today),
                ],
                record_id=record.id,
                highlight=still_inside,
                overdue=overdue,
            )
            if record.id == selected_id:
                self.today_table.selectRow(self.today_table.rowCount() - 1)

        self.inside_label.setText(f"On the platform now: {inside}        Date: {today:%Y-%m-%d}")

    def refresh_checkin_employees(self):
        # Only people who are NOT already on the platform can check in
        department_id = self.checkin_department.currentData()

        self.checkin_employee.blockSignals(True)
        self.checkin_employee.clear()
        if department_id is not None:
            open_ids = services.get_open_employee_ids()
            for employee in services.list_employees(department_id):
                if employee.id not in open_ids:
                    self.checkin_employee.addItem(employee.name, (employee.id, employee.badge))
        self.checkin_employee.setCurrentIndex(-1)   # nobody selected yet
        self.checkin_employee.blockSignals(False)

        self.update_badge_label()

    # =============================================================
    # TAB 2: HISTORY  (who took service, and when?)
    # =============================================================
    def build_history_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        filters = QGroupBox("Search past records  (leave a box empty to ignore it)")
        grid = QGridLayout(filters)

        self.all_dates = QCheckBox("All dates")
        self.date_from = QDateEdit(QDate.currentDate())
        self.date_to = QDateEdit(QDate.currentDate())
        for date_box in (self.date_from, self.date_to):
            date_box.setCalendarPopup(True)           # click the arrow to pick from a calendar
            date_box.setDisplayFormat("yyyy-MM-dd")
        self.history_department = QComboBox()
        self.name_filter = QLineEdit()
        self.name_filter.setPlaceholderText("part of the name")
        self.badge_filter = QLineEdit()
        self.badge_filter.setPlaceholderText("exact badge")

        grid.addWidget(self.all_dates, 0, 0)
        grid.addWidget(QLabel("From"), 0, 1)
        grid.addWidget(self.date_from, 0, 2)
        grid.addWidget(QLabel("To"), 0, 3)
        grid.addWidget(self.date_to, 0, 4)
        grid.addWidget(QLabel("Structure"), 0, 5)
        grid.addWidget(self.history_department, 0, 6)
        grid.addWidget(QLabel("Employee name"), 1, 1)
        grid.addWidget(self.name_filter, 1, 2)
        grid.addWidget(QLabel("Badge number"), 1, 3)
        grid.addWidget(self.badge_filter, 1, 4)

        search_button = QPushButton("Search")
        export_button = QPushButton("Export to Excel...")
        buttons = QHBoxLayout()
        buttons.addWidget(search_button)
        buttons.addWidget(export_button)
        grid.addLayout(buttons, 1, 5, 1, 2)
        layout.addWidget(filters)

        self.all_dates.toggled.connect(self.all_dates_toggled)
        search_button.clicked.connect(self.search_clicked)
        export_button.clicked.connect(self.export_clicked)
        self.name_filter.returnPressed.connect(self.search_clicked)
        self.badge_filter.returnPressed.connect(self.search_clicked)

        self.result_label = QLabel("")
        self.result_label.setStyleSheet(BIG_BOLD)
        layout.addWidget(self.result_label)

        self.history_table = make_table(
            [
                "Date",
                "Employee",
                "Structure",
                "Badge",
                "Phone number",
                "ID card number",
                "Time in",
                "Time out",
                "Time on platform",
            ],
            [100, 180, 150, 80, 140, 130, 140, 140, 130],
        )
        layout.addWidget(self.history_table)
        return tab

    def all_dates_toggled(self, checked):
        self.date_from.setEnabled(not checked)
        self.date_to.setEnabled(not checked)

    def search_clicked(self):
        if self.all_dates.isChecked():
            date_from = date_to = None
        else:
            date_from = self.date_from.date().toPython()   # QDate -> normal Python date
            date_to = self.date_to.date().toPython()

        try:
            records = services.search_records(
                date_from,
                date_to,
                self.history_department.currentData(),   # None means "All departments"
                self.name_filter.text(),
                self.badge_filter.text(),
            )
        except RuleError as error:
            self.error("Check your search", str(error))
            return

        self.last_search = records
        self.history_table.setRowCount(0)
        for record in records:
            add_row(self.history_table, history_values(record), record_id=record.id)
        self.result_label.setText(f"{len(records)} record(s) found")

    def export_clicked(self):
        if not self.last_search:
            self.info("Nothing to export", "Search first, then export the results.")
            return

        path = self.ask_save_path(
            "Export to Excel",
            f"attendance_{date.today():%Y-%m-%d}.xlsx",
            "Excel workbook (*.xlsx)",
        )
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"

        try:
            reports.export_to_excel(self.last_search, path)
        except (OSError, ValueError) as error:
            self.error(
                "Export failed",
                f"The file could not be saved.\n\n{error}\n\nIf it is open in Excel, close it and try again.",
            )
            return

        self.statusBar().showMessage(f"{len(self.last_search)} record(s) exported to {path}")

    # =============================================================
    # TAB 3: EMPLOYEES  (organised by structure)
    # =============================================================
    def build_employees_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        forms = QHBoxLayout()

        department_form = QGroupBox("New structure")
        department_layout = QHBoxLayout(department_form)
        self.new_department_entry = QLineEdit()
        self.new_department_entry.setPlaceholderText("structure name")
        add_department_button = QPushButton("Add structure")
        department_layout.addWidget(self.new_department_entry)
        department_layout.addWidget(add_department_button)
        forms.addWidget(department_form)

        employee_form = QGroupBox("New employee  (each employee has their own badge, never shared)")
        employee_layout = QHBoxLayout(employee_form)
        self.new_employee_entry = QLineEdit()
        self.new_employee_entry.setPlaceholderText("employee name")
        self.employee_form_department = QComboBox()
        make_searchable_combo(self.employee_form_department, "Type or choose a structure")
        self.new_badge_entry = QLineEdit()
        self.new_badge_entry.setPlaceholderText("badge number")
        self.new_phone_entry = QLineEdit()
        self.new_phone_entry.setPlaceholderText("phone (9 digits)")
        self.new_phone_entry.setToolTip("Enter exactly 9 digits.")
        self.new_id_card_entry = QLineEdit()
        self.new_id_card_entry.setPlaceholderText("ID card number")
        self.new_id_card_entry.setMaxLength(50)
        self.new_id_card_entry.setToolTip("Use 1 to 50 letters or digits.")
        add_employee_button = QPushButton("Add employee")
        employee_layout.addWidget(self.new_employee_entry, 2)
        employee_layout.addWidget(QLabel("in"))
        employee_layout.addWidget(self.employee_form_department, 1)
        employee_layout.addWidget(self.new_badge_entry, 1)
        employee_layout.addWidget(self.new_phone_entry, 1)
        employee_layout.addWidget(self.new_id_card_entry, 1)
        employee_layout.addWidget(add_employee_button)
        forms.addWidget(employee_form, 1)
        layout.addLayout(forms)

        add_department_button.clicked.connect(self.add_department_clicked)
        self.new_department_entry.returnPressed.connect(self.add_department_clicked)
        add_employee_button.clicked.connect(self.add_employee_clicked)
        self.new_employee_entry.returnPressed.connect(self.add_employee_clicked)
        self.new_badge_entry.returnPressed.connect(self.add_employee_clicked)
        self.new_phone_entry.returnPressed.connect(self.add_employee_clicked)
        self.new_id_card_entry.returnPressed.connect(self.add_employee_clicked)

        self.show_inactive = QCheckBox("Show deactivated employees")
        self.show_inactive.toggled.connect(self.refresh_employees)
        layout.addWidget(self.show_inactive)

        self.employee_search = QLineEdit()
        self.employee_search.setPlaceholderText("Search employees or structures")
        self.employee_search.setClearButtonEnabled(True)
        self.employee_search.textChanged.connect(self.refresh_employees)
        layout.addWidget(self.employee_search)

        # A tree: each department is a parent row, and its employees are inside it
        self.employee_tree = QTreeWidget()
        self.employee_tree.setHeaderLabels(
            ["Structure / Employee", "Badge", "Phone number", "ID card number", "Status"]
        )
        self.employee_tree.setColumnWidth(0, 240)
        self.employee_tree.setColumnWidth(1, 90)
        self.employee_tree.setColumnWidth(2, 150)
        self.employee_tree.setColumnWidth(3, 130)
        layout.addWidget(self.employee_tree)

        buttons = QHBoxLayout()
        change_badge_button = QPushButton("Change badge number")
        edit_details_button = QPushButton("Update phone and ID card")
        deactivate_button = QPushButton("Deactivate selected employee")
        reactivate_button = QPushButton("Reactivate selected employee")
        buttons.addWidget(change_badge_button)
        buttons.addWidget(edit_details_button)
        buttons.addWidget(deactivate_button)
        buttons.addWidget(reactivate_button)
        layout.addLayout(buttons)
        change_badge_button.clicked.connect(self.change_badge_clicked)
        edit_details_button.clicked.connect(self.edit_employee_details_clicked)
        deactivate_button.clicked.connect(self.deactivate_clicked)
        reactivate_button.clicked.connect(self.reactivate_clicked)
        return tab

    def add_department_clicked(self):
        name = self.new_department_entry.text()
        try:
            services.add_department(name)
        except RuleError as error:
            self.error("Cannot add structure", str(error))
            return

        self.new_department_entry.clear()
        self.statusBar().showMessage(f"Structure '{name.strip()}' added.")
        self.refresh_departments()
        self.refresh_checkin_employees()
        self.refresh_employees()

    def add_employee_clicked(self):
        department_id = self.employee_form_department.currentData()
        if department_id is None:
            self.warn("Missing information", "Choose the employee's structure.")
            return

        name = self.new_employee_entry.text()
        try:
            badge = services.add_employee(
                name,
                department_id,
                self.new_badge_entry.text(),
                self.new_phone_entry.text(),
                self.new_id_card_entry.text(),
            )
        except RuleError as error:
            self.error("Cannot add employee", str(error))
            return

        self.new_employee_entry.clear()
        self.new_badge_entry.clear()
        self.new_phone_entry.clear()
        self.new_id_card_entry.clear()
        self.statusBar().showMessage(f"{' '.join(name.split())} added with badge {badge}.")
        self.refresh_employees()
        self.refresh_checkin_employees()

    def selected_employee(self):
        # Returns (employee id, name, status), or (None, None, None) if a department
        # (or nothing) is selected
        items = self.employee_tree.selectedItems()
        if not items:
            return None, None, None
        employee_id = items[0].data(0, Qt.ItemDataRole.UserRole)
        return employee_id, items[0].text(0), items[0].text(4)

    def edit_employee_details_clicked(self):
        employee_id, name, _ = self.selected_employee()
        if employee_id is None:
            self.warn("Nothing selected", "Click an employee (not a structure) first.")
            return

        employee = next(
            (person for person in services.list_employees(include_inactive=True) if person.id == employee_id),
            None,
        )
        if employee is None:
            self.error("Employee not found", "Refresh the employee list and try again.")
            return

        phone, accepted = QInputDialog.getText(
            self,
            "Update employee details",
            f"Phone number for {name} (exactly 9 digits):",
            text=employee.phone_number or "",
        )
        if not accepted:
            return
        id_card, accepted = QInputDialog.getText(
            self,
            "Update employee details",
            f"ID card number for {name} (1 to 50 letters or digits):",
            text=str(employee.id_card_number) if employee.id_card_number is not None else "",
        )
        if not accepted:
            return

        try:
            services.update_employee_details(employee_id, phone, id_card)
        except RuleError as error:
            self.error("Cannot update employee details", str(error))
            return

        self.statusBar().showMessage(f"{name}'s phone and ID card details were updated.")
        self.refresh_employees()
        self.refresh_today()
        self.search_clicked()

    def change_badge_clicked(self):
        employee_id, name, _ = self.selected_employee()
        if employee_id is None:
            self.warn("Nothing selected", "Click an employee (not a structure) first.")
            return

        badge, accepted = QInputDialog.getText(
            self,
            "Change badge number",
            f"Enter the new badge number for {name}:",
        )
        if not accepted:
            return

        try:
            badge = services.change_employee_badge(employee_id, badge)
        except RuleError as error:
            self.error("Cannot change badge number", str(error))
            return

        self.statusBar().showMessage(f"{name}'s badge number changed to {badge}.")
        self.refresh_employees()
        self.refresh_checkin_employees()

    def deactivate_clicked(self):
        employee_id, name, status = self.selected_employee()
        if employee_id is None:
            self.warn("Nothing selected", "Click an employee (not a structure) first.")
            return

        if not self.confirm(
            "Deactivate employee",
            f"Deactivate {name}?\n\nThey will no longer appear in the check-in list.\n"
            "Their badge stays reserved for them, and all their past records are kept.",
        ):
            return

        try:
            services.deactivate_employee(employee_id)
        except RuleError as error:
            self.error("Cannot deactivate", str(error))
            return

        self.statusBar().showMessage(f"{name} deactivated. Past records are kept.")
        self.refresh_employees()
        self.refresh_checkin_employees()

    def reactivate_clicked(self):
        employee_id, name, status = self.selected_employee()
        if employee_id is None:
            self.warn(
                "Nothing selected",
                "Tick 'Show deactivated employees', then click the employee to bring back.",
            )
            return
        if status != "Deactivated":
            self.info("Already active", f"{name} is already active.")
            return

        try:
            services.reactivate_employee(employee_id)
        except RuleError as error:
            self.error("Cannot reactivate", str(error))
            return

        self.statusBar().showMessage(f"{name} is back (reactivated).")
        self.refresh_employees()
        self.refresh_checkin_employees()

    def refresh_employees(self):
        self.employee_tree.clear()
        open_ids = services.get_open_employee_ids()
        search_text = fold(self.employee_search.text().strip())

        # Group the employees by department
        members_by_department = {}
        for employee in services.list_employees(include_inactive=self.show_inactive.isChecked()):
            members_by_department.setdefault(employee.department_id, []).append(employee)

        for department_id, department_name in services.list_departments():
            department_members = members_by_department.get(department_id, [])
            department_matches = not search_text or search_text in fold(department_name)
            members = department_members
            if search_text and not department_matches:
                members = [
                    employee
                    for employee in members
                    if any(
                        search_text in fold(value or "")
                        for value in (
                            employee.name,
                            employee.phone_number,
                            str(employee.id_card_number or ""),
                        )
                    )
                ]
                if not members:
                    continue
            active_count = sum(1 for employee in department_members if employee.active)

            parent = QTreeWidgetItem([f"{department_name}   ({active_count})", "", "", "", ""])
            self.employee_tree.addTopLevelItem(parent)

            employees = department_members if department_matches else members
            for employee in employees:
                if not employee.active:
                    status = "Deactivated"
                elif employee.id in open_ids:
                    status = "On platform"
                else:
                    status = ""
                child = QTreeWidgetItem(
                    [
                        employee.name,
                        employee.badge,
                        employee.phone_number or "Not set",
                        str(employee.id_card_number) if employee.id_card_number is not None else "Not set",
                        status,
                    ]
                )
                child.setData(0, Qt.ItemDataRole.UserRole, employee.id)
                if not employee.active:
                    for column in range(5):
                        child.setForeground(column, GREY)
                parent.addChild(child)

        self.employee_tree.expandAll()

    # =============================================================
    # KEEPING EVERYTHING UP TO DATE
    # =============================================================
    def refresh_departments(self):
        departments = services.list_departments()

        for combo, has_all_option in (
            (self.checkin_department, False),
            (self.employee_form_department, False),
            (self.history_department, True),
            (self.today_department_filter, True),
        ):
            chosen = combo.currentData()   # remember the current choice

            combo.blockSignals(True)       # don't react while we refill the list
            combo.clear()
            if has_all_option:
                combo.addItem(ALL_DEPARTMENTS, None)
            for department_id, name in departments:
                combo.addItem(name, department_id)

            index = combo.findData(chosen) if chosen is not None else -1
            if index < 0:
                index = 0 if has_all_option else -1
            combo.setCurrentIndex(index)
            combo.blockSignals(False)

    def refresh_all(self):
        self.refresh_departments()
        self.refresh_checkin_employees()
        self.refresh_today()
        self.refresh_employees()
