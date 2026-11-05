"""
self_check.py -- run this FIRST, right after installing the requirements.

    python self_check.py          checks the rules, the database and the Excel export
    python self_check.py --gui    also drives the real window (no screen needed)

It uses a temporary database, so it never touches your real records.
Every line says OK or FAILED, so you know exactly what to look at.
"""

import os
import shutil
import sys
import tempfile
import traceback
from datetime import date, datetime
from unittest.mock import patch

try:
    from openpyxl import load_workbook

    import models
    import reports
    import services
    from common import RuleError
except ImportError as error:
    print(f"A library is missing: {error}")
    print("Install everything first:  pip install -r requirements.txt")
    sys.exit(1)

failures = []


def check(label, condition):
    print(f"  {'OK    ' if condition else 'FAILED'}  {label}")
    if not condition:
        failures.append(label)


def expect_error(label, function, *args, contains="", **kwargs):
    # The rule is respected if the function refuses with a RuleError
    # whose message contains the given words
    try:
        function(*args, **kwargs)
    except RuleError as error:
        check(label, contains.lower() in str(error).lower())
        return
    check(label + "  (it was NOT refused)", False)


def t(text):
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")


def check_legacy_database_migration(folder):
    import sqlite3

    path = os.path.join(folder, "legacy.db")
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE department (
            id INTEGER PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            name_key VARCHAR(100) NOT NULL UNIQUE
        );
        CREATE TABLE employee (
            id INTEGER PRIMARY KEY,
            name VARCHAR(150) NOT NULL,
            name_key VARCHAR(150) NOT NULL,
            department_id INTEGER NOT NULL REFERENCES department(id),
            badge_number VARCHAR(50) NOT NULL UNIQUE,
            active BOOLEAN NOT NULL
        );
        CREATE TABLE attendance (
            id INTEGER PRIMARY KEY,
            employee_id INTEGER NOT NULL REFERENCES employee(id),
            time_in DATETIME NOT NULL,
            time_out DATETIME
        );
        INSERT INTO department VALUES (1, 'Security', 'security');
        INSERT INTO employee VALUES (1, 'Legacy Employee', 'legacy employee', 1, 'OLD1', 1);
        INSERT INTO attendance VALUES (1, 1, '2026-10-19 08:00:00', '2026-10-19 09:00:00');
        """
    )
    connection.commit()
    connection.close()

    models.init_db(path)
    records = services.search_records()
    check(
        "existing databases migrate and retain attendance badge values",
        len(records) == 1 and records[0].badge == "OLD1",
    )
    models.engine.dispose()


# ---------------------------------------------------------------
# 1. THE RULES AND THE DATABASE
# ---------------------------------------------------------------

def check_services(folder):
    path = os.path.join(folder, "rules.db")
    models.init_db(path)
    models.init_db(path)   # starting twice must be harmless

    services.add_department("  Security ")
    services.add_department("Finance")
    services.add_department("Éducation")
    expect_error("same department, other capitals: refused", services.add_department, "FINANCE", contains="already exists")
    expect_error("same department, no accent: refused", services.add_department, "education", contains="already exists")
    expect_error("empty department name: refused", services.add_department, "   ", contains="enter a department")
    departments = {name: department_id for department_id, name in services.list_departments()}
    check("departments sorted, Éducation under E", list(departments) == ["Éducation", "Finance", "Security"])

    badge = services.add_employee("Alice  Ngu", departments["Security"], " a12 ")
    check("badge is saved in capitals, spaces removed", badge == "A12")
    services.add_employee("Bob Tamba", departments["Finance"], "B7")
    services.add_employee("Éric Mbarga", departments["Finance"], "B5")
    services.add_employee("Alice Ngu", departments["Finance"], "C3")   # same name, other department: allowed
    expect_error("same person twice: refused", services.add_employee, "alice ngu", departments["Security"], "Z1", contains="already registered")
    expect_error("same person without the accent: refused", services.add_employee, "Eric Mbarga", departments["Finance"], "Z2", contains="already registered")
    expect_error("a badge owned by someone else: refused", services.add_employee, "New Person", departments["Security"], "b7", contains="already belongs to Bob Tamba")
    expect_error("empty badge: refused", services.add_employee, "New Person", departments["Security"], "  ", contains="badge number")
    expect_error("empty name: refused", services.add_employee, "", departments["Security"], "Z3", contains="enter the employee")

    listing = [(e.department, e.name) for e in services.list_employees()]
    check(
        "employees sorted by department, then name",
        listing == [("Finance", "Alice Ngu"), ("Finance", "Bob Tamba"), ("Finance", "Éric Mbarga"), ("Security", "Alice Ngu")],
    )
    people = {(e.name, e.department): e for e in services.list_employees()}
    alice_sec = people[("Alice Ngu", "Security")].id
    alice_fin = people[("Alice Ngu", "Finance")].id
    bob = people[("Bob Tamba", "Finance")].id
    eric = people[("Éric Mbarga", "Finance")].id

    name, badge, time_in = services.check_in(alice_sec, now=t("2026-10-19 08:00:00"))
    check("check-in gives the name, THEIR badge and the time", (name, badge, time_in) == ("Alice Ngu", "A12", t("2026-10-19 08:00:00")))
    expect_error("checking in twice: refused", services.check_in, alice_sec, contains="already on the platform")
    expect_error("unknown employee: refused", services.check_in, 99999, contains="not found")
    check("she is now counted as on the platform", services.get_open_employee_ids() == {alice_sec})

    today = services.get_today_records(today=date(2026, 10, 19))
    check("today's list shows the open visit", len(today) == 1 and today[0].time_out is None and today[0].badge == "A12")
    visit_id = today[0].id
    expect_error("clock set back before check-in: refused", services.check_out, visit_id, now=t("2026-10-19 07:00:00"), contains="clock")
    name, badge, time_out = services.check_out(visit_id, now=t("2026-10-19 16:30:00"))
    check("check-out gives the name, badge and time", (name, badge, time_out) == ("Alice Ngu", "A12", t("2026-10-19 16:30:00")))
    expect_error("checking out twice: refused", services.check_out, visit_id, contains="already left")
    expect_error("unknown record: refused", services.check_out, 99999, contains="not found")
    check("nobody is inside any more", services.get_open_employee_ids() == set())

    # Build some history over several days
    services.check_in(bob, now=t("2026-10-19 17:00:00"))
    services.check_out(services.search_records()[0].id, now=t("2026-10-19 18:00:00"))
    services.check_in(eric, now=t("2026-10-20 08:10:00"))
    services.check_out(services.search_records()[0].id, now=t("2026-10-20 12:00:00"))
    services.check_in(alice_fin, now=t("2026-10-20 22:00:00"))    # night shift, still inside
    services.check_in(alice_sec, now=t("2026-10-21 08:05:00"))

    today = services.get_today_records(today=date(2026, 10, 21))
    check("today's list also shows the night-shift person still inside", sorted(r.employee for r in today) == ["Alice Ngu", "Alice Ngu"] and {r.department for r in today} == {"Finance", "Security"})
    expect_error(
        "cannot change a badge while its employee is on the platform",
        services.change_employee_badge,
        alice_sec,
        "A99",
        contains="on the platform",
    )

    def search(**filters):
        return services.search_records(**filters)

    check("search: everything = 5 visits", len(search()) == 5)
    check("search: one day", len(search(date_from=date(2026, 10, 19), date_to=date(2026, 10, 19))) == 2)
    check("search: from a date", len(search(date_from=date(2026, 10, 20))) == 3)
    check("search: up to a date", len(search(date_to=date(2026, 10, 19))) == 2)
    check("search: one department", len(search(department_id=departments["Security"])) == 2)
    check("search: name, any capitals and accents", [r.employee for r in search(name_text="ERIC")] == ["Éric Mbarga"])
    check("search: part of a name", [r.employee for r in search(name_text="mbar")] == ["Éric Mbarga"])
    check("search: 'alice' finds her in both departments", len(search(name_text="alice")) == 3)
    check("search: by badge", [r.employee for r in search(badge_text="a12")] == ["Alice Ngu", "Alice Ngu"])
    check("search: filters combined", len(search(date_from=date(2026, 10, 19), name_text="alice", department_id=departments["Security"])) == 2)
    check("search: nobody found", search(name_text="nobody") == [])
    check("search: a typed % is just a character", search(name_text="%") == [])
    times = [r.time_in for r in search()]
    check("search: newest visit first", times == sorted(times, reverse=True))
    expect_error("search: 'From' after 'To': refused", services.search_records, date_from=date(2026, 10, 21), date_to=date(2026, 10, 19), contains="later")

    expect_error("cannot deactivate someone on the platform", services.deactivate_employee, alice_fin, contains="on the platform")
    overnight_visit = next(
        record
        for record in services.get_today_records(today=date(2026, 10, 21))
        if record.employee == "Alice Ngu" and record.department == "Finance" and record.time_out is None
    )
    services.check_out(overnight_visit.id, now=t("2026-10-21 16:00:00"))
    closed_today_records = services.get_today_records(today=date(2026, 10, 21))
    check(
        "today retains an overnight visit checked out today, including logout time",
        any(
            record.id == overnight_visit.id
            and record.time_in == t("2026-10-20 22:00:00")
            and record.time_out == t("2026-10-21 16:00:00")
            for record in closed_today_records
        ),
    )
    services.deactivate_employee(eric)
    check("deactivated: gone from the normal list", eric not in [e.id for e in services.list_employees()])
    check("deactivated: still there when asked for", eric in [e.id for e in services.list_employees(include_inactive=True)])
    check("deactivated: his history is kept", [r.employee for r in search(name_text="eric")] == ["Éric Mbarga"])
    expect_error("deactivated: cannot check in", services.check_in, eric, contains="deactivated")
    expect_error("deactivated: his badge stays reserved", services.add_employee, "Someone New", departments["Finance"], "B5", contains="already belongs to Éric Mbarga")
    expect_error("deactivated: adding him again points to Reactivate", services.add_employee, "éric mbarga", departments["Finance"], "Q9", contains="deactivated")
    services.reactivate_employee(eric)
    check("reactivated: back in the normal list", eric in [e.id for e in services.list_employees()])

    backup_path = os.path.join(folder, "backup.db")
    services.backup_database(backup_path)
    import sqlite3
    copy = sqlite3.connect(backup_path)
    visits_in_copy = copy.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]
    copy.close()
    check("backup contains all 5 visits", visits_in_copy == 5)
    expect_error("backup onto the live database: refused", services.backup_database, models.DB_PATH, contains="different file")

    services.check_out(
        next(record.id for record in services.get_today_records(today=date(2026, 10, 21)) if record.employee == "Alice Ngu" and record.department == "Security" and record.time_out is None),
        now=t("2026-10-21 16:00:00"),
    )
    check(
        "badge change returns the normalized new badge",
        services.change_employee_badge(alice_sec, " a99 ") == "A99",
    )
    check(
        "previous visits keep their original badge",
        [record.badge for record in services.search_records(badge_text="a12")] == ["A12", "A12"],
    )
    expect_error(
        "a former badge cannot be assigned to another employee",
        services.add_employee,
        "Former Badge Attempt",
        departments["Security"],
        "A12",
        contains="previously used by Alice Ngu",
    )
    expect_error(
        "a badge currently owned by another employee cannot be assigned",
        services.change_employee_badge,
        alice_sec,
        "B7",
        contains="already belongs to Bob Tamba",
    )
    services.check_in(alice_sec, now=t("2026-10-22 08:00:00"))
    check(
        "future visits use the new badge",
        [record.badge for record in services.search_records(badge_text="a99")] == ["A99"],
    )
    check(
        "historical badge search excludes visits with the new badge",
        len(services.search_records(badge_text="a12")) == 2,
    )


def check_safety_net():
    # The database must refuse impossible situations ITSELF, even if the
    # program had a bug. This talks to the database directly, on purpose.
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    inside_id = next(iter(services.get_open_employee_ids()))
    with Session(models.engine) as session:
        session.add(models.Attendance(employee_id=inside_id, badge_number="TEST", time_in=datetime.now()))
        try:
            session.commit()
            check("database refuses a second open visit for the same person", False)
        except IntegrityError:
            session.rollback()
            check("database refuses a second open visit for the same person", True)

    department_id = services.list_departments()[0][0]
    with Session(models.engine) as session:
        session.add(models.Employee(name="Copy", name_key="copy", department_id=department_id, badge_number="A99", active=True))
        try:
            session.commit()
            check("database refuses the same badge for two employees", False)
        except IntegrityError:
            session.rollback()
            check("database refuses the same badge for two employees", True)


# ---------------------------------------------------------------
# 2. THE EXCEL REPORT
# ---------------------------------------------------------------

def check_reports(folder):
    records = services.search_records()
    path = os.path.join(folder, "report.xlsx")
    reports.export_to_excel(records, path)

    workbook = load_workbook(path)
    check("Excel file has the 3 sheets", workbook.sheetnames == ["Records", "By department", "By employee"])
    sheet = workbook["Records"]
    check("Excel 'Records': header + one row per visit", sheet.max_row == len(records) + 1)
    check("Excel 'Records': header is correct", [c.value for c in sheet[1]] == reports.DETAIL_COLUMNS)
    check("Excel keeps the accents", any(c.value == "Éric Mbarga" for row in sheet.iter_rows() for c in row))
    check("Excel 'By department' has a row per department", workbook["By department"].max_row == 3)


# ---------------------------------------------------------------
# 3. THE WINDOW (only with --gui)
# ---------------------------------------------------------------

def run_window_scenario(window, folder):
    from PySide6.QtTest import QTest

    # Drives the window like a person would: types, picks, clicks.
    messages = []
    answers = {"confirm": True}
    window.warn = lambda title, text: messages.append(("warning", title, text))
    window.error = lambda title, text: messages.append(("error", title, text))
    window.info = lambda title, text: messages.append(("info", title, text))
    window.confirm = lambda title, text: answers["confirm"]
    export_path = os.path.join(folder, "window_export.xlsx")
    window.ask_save_path = lambda title, default_name, file_filter: export_path

    def choose(combo, text):
        index = combo.findText(text)
        check(f"'{text}' is in the list", index >= 0)
        combo.setCurrentIndex(index)

    def cell(table, row, column):
        return table.item(row, column).text()

    check("starts with an empty Today table", window.today_table.rowCount() == 0)
    check("shows 0 people on the platform", "On the platform now: 0" in window.inside_label.text())

    # --- departments and employees
    for name in ("Security", "Finance"):
        window.new_department_entry.setText(name)
        window.add_department_clicked()
    check("both departments are in the check-in list", window.checkin_department.count() == 2)
    check("History list has 'All departments' + the 2", window.history_department.count() == 3)
    check("Today department selector is searchable and still lists its options", window.checkin_department.isEditable() and window.checkin_department.count() == 2)
    window.checkin_department.setFocus()
    window.checkin_department.lineEdit().selectAll()
    QTest.keyClicks(window.checkin_department.lineEdit(), "fin")
    department_matches = window.checkin_department.completer().completionModel()
    check(
        "typing filters department suggestions",
        department_matches.rowCount() == 1
        and department_matches.index(0, 0).data() == "Finance",
    )
    window.checkin_department.lineEdit().clear()
    window.new_department_entry.setText("security")
    window.add_department_clicked()
    check("duplicate department shows an error", messages[-1][0] == "error")
    window.new_department_entry.clear()

    check(
        "employee department selector is searchable and lists departments",
        window.employee_form_department.isEditable()
        and window.employee_form_department.count() == 2,
    )
    window.employee_form_department.setFocus()
    window.employee_form_department.lineEdit().selectAll()
    QTest.keyClicks(window.employee_form_department.lineEdit(), "fin")
    employee_department_matches = window.employee_form_department.completer().completionModel()
    check(
        "typing filters employee department suggestions",
        employee_department_matches.rowCount() == 1
        and employee_department_matches.index(0, 0).data() == "Finance",
    )
    window.employee_form_department.lineEdit().clear()

    for name, department, badge in (("Alice Ngu", "Security", "a12"), ("Bob Tamba", "Finance", "B7")):
        choose(window.employee_form_department, department)
        window.new_employee_entry.setText(name)
        window.new_badge_entry.setText(badge)
        window.add_employee_clicked()
    tree = window.employee_tree
    check("tree shows the departments with their counts", [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())] == ["Finance   (1)", "Security   (1)"])
    check("the employee is inside her department", tree.topLevelItem(1).child(0).text(0) == "Alice Ngu" and tree.topLevelItem(1).child(0).text(1) == "A12")

    window.employee_search.setText("FINANCE")
    check(
        "employee search finds a department",
        tree.topLevelItemCount() == 1
        and tree.topLevelItem(0).text(0).startswith("Finance")
        and tree.topLevelItem(0).childCount() == 1,
    )
    window.employee_search.setText("alice")
    check(
        "employee search finds a person",
        tree.topLevelItemCount() == 1
        and tree.topLevelItem(0).text(0).startswith("Security")
        and tree.topLevelItem(0).childCount() == 1
        and tree.topLevelItem(0).child(0).text(0) == "Alice Ngu",
    )
    window.employee_search.setText("not found")
    check("employee search shows no results when unmatched", tree.topLevelItemCount() == 0)
    window.employee_search.clear()
    check("clearing employee search restores the full tree", tree.topLevelItemCount() == 2)

    choose(window.employee_form_department, "Finance")
    window.new_employee_entry.setText("Bobby")
    window.new_badge_entry.setText("a12")
    window.add_employee_clicked()
    check("a badge already owned is refused", messages[-1][0] == "error" and "already belongs to Alice Ngu" in messages[-1][2])
    check("the typed text stays so it can be corrected", window.new_employee_entry.text() == "Bobby")
    window.new_employee_entry.clear()
    window.new_badge_entry.clear()

    # --- check in
    choose(window.checkin_department, "Security")
    check("employee list shows the people of that department", window.checkin_employee.count() == 1)
    check("Today employee selector is searchable and lists its employees", window.checkin_employee.isEditable() and window.checkin_employee.count() == 1)
    window.checkin_employee.setFocus()
    window.checkin_employee.lineEdit().selectAll()
    QTest.keyClicks(window.checkin_employee.lineEdit(), "ali")
    employee_matches = window.checkin_employee.completer().completionModel()
    check(
        "typing filters employee suggestions",
        employee_matches.rowCount() == 1
        and employee_matches.index(0, 0).data() == "Alice Ngu",
    )
    window.checkin_employee.lineEdit().clear()
    window.check_in_clicked()
    check("checking in with nobody chosen shows a warning", messages[-1][0] == "warning")
    choose(window.checkin_employee, "Alice Ngu")
    check("her own badge is shown", window.badge_label.text() == "Badge: A12")
    window.check_in_clicked()
    check("one row in the Today table", window.today_table.rowCount() == 1)
    check("row shows name, department and badge", [cell(window.today_table, 0, c) for c in (0, 1, 2)] == ["Alice Ngu", "Security", "A12"])
    check("time out is still empty", cell(window.today_table, 0, 4) == "")
    check(
        "Today department filter lists registered departments",
        [window.today_department_filter.itemText(i) for i in range(window.today_department_filter.count())]
        == ["All departments", "Finance", "Security"],
    )
    choose(window.today_department_filter, "Finance")
    check("Today department filter hides rows from other departments", window.today_table.rowCount() == 0)
    choose(window.today_department_filter, "Security")
    check(
        "Today department filter shows the selected department",
        window.today_table.rowCount() == 1
        and cell(window.today_table, 0, 1) == "Security",
    )
    choose(window.today_department_filter, "All departments")
    check("Today department filter can show all departments", window.today_table.rowCount() == 1)
    check("1 person on the platform", "On the platform now: 1" in window.inside_label.text())
    check("she can't be picked again while inside", window.checkin_employee.count() == 0)

    window.today_search.setText("alice")
    check("Today search finds an employee by name", window.today_table.rowCount() == 1)
    window.today_search.setText("security")
    check("Today search finds an employee by department", window.today_table.rowCount() == 1)
    window.today_search.setText("a12")
    check("Today search finds an employee by badge", window.today_table.rowCount() == 1)
    window.today_search.setText("not found")
    check(
        "Today search hides non-matching rows without changing the inside count",
        window.today_table.rowCount() == 0
        and "On the platform now: 1" in window.inside_label.text(),
    )
    window.today_search.clear()
    check("clearing Today search restores the table", window.today_table.rowCount() == 1)

    # --- check out
    window.today_table.clearSelection()
    window.check_out_clicked()
    check("checking out with nothing selected shows a warning", messages[-1][0] == "warning")
    window.today_table.selectRow(0)
    answers["confirm"] = False
    window.check_out_clicked()
    check("answering No changes nothing", cell(window.today_table, 0, 4) == "")
    answers["confirm"] = True
    window.check_out_clicked()
    check("answering Yes fills the time out", cell(window.today_table, 0, 4) != "")
    check("checked-out employee remains visible in Today", window.today_table.rowCount() == 1)
    check("0 people on the platform again", "On the platform now: 0" in window.inside_label.text())
    check("she is available for check-in again", window.checkin_employee.count() == 1)
    window.check_out_clicked()
    check("checking out someone who already left is explained", messages[-1][0] == "info")

    # --- history and Excel
    window.search_clicked()
    check("History finds today's visit", window.result_label.text() == "1 record(s) found")
    window.name_filter.setText("zzz")
    window.search_clicked()
    check("History: a name nobody has finds 0", window.result_label.text() == "0 record(s) found")
    window.name_filter.setText("alice")
    window.search_clicked()
    check("History: filter by name", window.result_label.text() == "1 record(s) found")
    window.name_filter.clear()
    window.badge_filter.setText("a12")
    window.search_clicked()
    check("History: filter by badge", window.result_label.text() == "1 record(s) found")
    window.badge_filter.clear()
    window.all_dates.setChecked(True)
    check("'All dates' disables the two date boxes", not window.date_from.isEnabled() and not window.date_to.isEnabled())
    window.search_clicked()
    check("History: all dates still finds the visit", window.result_label.text() == "1 record(s) found")
    window.export_clicked()
    check("Excel file was created", os.path.exists(export_path) and "Records" in load_workbook(export_path).sheetnames)

    tree.setCurrentItem(tree.topLevelItem(1).child(0))
    with patch("main_window.QInputDialog.getText", return_value=("A99", True)):
        window.change_badge_clicked()
    check("employee tab shows the changed badge", tree.topLevelItem(1).child(0).text(1) == "A99")
    window.search_clicked()
    check(
        "history retains the badge used before the change",
        window.history_table.rowCount() == 1
        and cell(window.history_table, 0, 3) == "A12",
    )

    # --- deactivate / reactivate
    tree.setCurrentItem(tree.topLevelItem(0))
    window.deactivate_clicked()
    check("selecting a department (not an employee) shows a warning", messages[-1][0] == "warning")
    tree.setCurrentItem(tree.topLevelItem(1).child(0))
    window.deactivate_clicked()
    check("deactivated employee disappears from the tree", tree.topLevelItem(1).childCount() == 0)
    window.show_inactive.setChecked(True)
    check("'Show deactivated' brings her back, marked", tree.topLevelItem(1).childCount() == 1 and tree.topLevelItem(1).child(0).text(2) == "Deactivated")
    tree.topLevelItem(1).child(0).setSelected(True)
    window.reactivate_clicked()
    check("reactivated: no longer marked", tree.topLevelItem(1).child(0).text(2) == "")
    check("reactivated: back in the check-in list", window.checkin_employee.count() == 1)


def check_window(folder):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"   # no screen needed
    from PySide6.QtWidgets import QApplication

    import main_window

    app = QApplication.instance() or QApplication([])
    models.init_db(os.path.join(folder, "window.db"))
    window = main_window.MainWindow()
    check("main window loads the app icon", not window.windowIcon().isNull())
    run_window_scenario(window, folder)
    window.close()
    return app


# ---------------------------------------------------------------
# RUN EVERYTHING
# ---------------------------------------------------------------

def run_section(title, function, *args):
    print(f"\n{title}")
    try:
        function(*args)
    except Exception:
        failures.append(f"{title}: crashed")
        print("  CRASHED  (the details are below; please send them to whoever helps you)")
        traceback.print_exc()


def main():
    folder = tempfile.mkdtemp(prefix="platform_check_")
    try:
        run_section("0. Existing database migration", check_legacy_database_migration, folder)
        run_section("1. Rules and database", check_services, folder)
        run_section("2. The database protects itself", check_safety_net)
        run_section("3. Excel report", check_reports, folder)
        if "--gui" in sys.argv:
            run_section("4. The window", check_window, folder)
        else:
            print("\n(The window was not tested. Add --gui to test it too.)")
    finally:
        if models.engine is not None:
            models.engine.dispose()   # let go of the database file before deleting it
        shutil.rmtree(folder, ignore_errors=True)

    print("\n" + "=" * 60)
    if failures:
        print(f"{len(failures)} problem(s):")
        for label in failures:
            print(f"  - {label}")
        sys.exit(1)
    print("Everything is OK.")


if __name__ == "__main__":
    main()
