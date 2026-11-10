"""
services.py -- everything the app can DO: add people, check in, check out, search.

The window (main_window.py) never touches the database directly.
It calls the functions below, and all the rules live here.
Every function opens a Session (a conversation with the database),
does its work, and closes it again.
"""

import os
import re
import sqlite3
from datetime import date, datetime, time, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models
from common import EmployeeInfo, Record, RuleError, clean_name, fold
from models import Attendance, Department, Employee


# ---------------------------------------------------------------
# DEPARTMENTS
# ---------------------------------------------------------------

def add_department(name):
    name = clean_name(name)
    if not name:
        raise RuleError("Please enter a structure name.")

    with Session(models.engine) as session:
        existing = session.scalar(select(Department).where(Department.name_key == fold(name)))
        if existing is not None:
            raise RuleError(f"The structure '{existing.name}' already exists.")

        session.add(Department(name=name, name_key=fold(name)))
        session.commit()


def list_departments():
    # Returns [(id, name), ...] in alphabetical order
    with Session(models.engine) as session:
        rows = session.execute(
            select(Department.id, Department.name).order_by(Department.name_key)
        ).all()
    return [(department_id, name) for department_id, name in rows]


# ---------------------------------------------------------------
# EMPLOYEES
# ---------------------------------------------------------------

def _validate_employee_details(phone_number, id_card_number):
    phone = (phone_number or "").strip()
    if not re.fullmatch(r"\+[0-9]{7,15}", phone):
        raise RuleError("Enter a phone number with + followed by 7 to 15 digits.")

    card_text = str(id_card_number or "").strip()
    if not re.fullmatch(r"[0-9]+", card_text):
        raise RuleError("Enter a positive integer ID card number.")
    card_number = int(card_text)
    if card_number < 1 or card_number > 9_223_372_036_854_775_807:
        raise RuleError("The ID card number must be a positive integer.")
    return phone, card_number


def add_employee(name, department_id, badge_number, phone_number, id_card_number):
    # Returns the badge number as it was saved (capitals, no spaces around it)
    name = clean_name(name)
    badge = (badge_number or "").strip().upper()
    phone, card_number = _validate_employee_details(phone_number, id_card_number)
    if not name:
        raise RuleError("Please enter the employee's name.")
    if not badge:
        raise RuleError("Please enter the employee's badge number.")

    with Session(models.engine) as session:
        department = session.get(Department, department_id)
        if department is None:
            raise RuleError("Please choose a structure.")

        same_name = session.scalar(
            select(Employee).where(
                Employee.name_key == fold(name),
                Employee.department_id == department_id,
            )
        )
        if same_name is not None:
            if same_name.active:
                raise RuleError(f"{same_name.name} is already registered in structure {department.name}.")
            raise RuleError(
                f"{same_name.name} is already registered in structure {department.name}, but deactivated.\n"
                "Tick 'Show deactivated employees' and use 'Reactivate'."
            )

        badge_owner = session.scalar(select(Employee).where(Employee.badge_number == badge))
        if badge_owner is not None:
            raise RuleError(
                f"Badge {badge} already belongs to {badge_owner.name}.\n"
                "A badge cannot be shared between employees."
            )

        former_owner_id = session.scalar(
            select(Attendance.employee_id)
            .where(Attendance.badge_number == badge)
            .limit(1)
        )
        if former_owner_id is not None:
            former_owner = session.get(Employee, former_owner_id)
            raise RuleError(
                f"Badge {badge} was previously used by {former_owner.name}.\n"
                "A badge cannot be shared between employees."
            )

        session.add(
            Employee(
                name=name,
                name_key=fold(name),
                department_id=department_id,
                badge_number=badge,
                phone_number=phone,
                id_card_number=card_number,
                active=True,
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise RuleError("This employee or this badge is already registered.")
    return badge


def list_employees(department_id=None, include_inactive=False):
    # Returns a list of EmployeeInfo, sorted by department and then by name
    statement = (
        select(
            Employee.id,
            Employee.name,
            Employee.department_id,
            Department.name,
            Employee.badge_number,
            Employee.phone_number,
            Employee.id_card_number,
            Employee.active,
        )
        .select_from(Employee)
        .join(Department, Employee.department_id == Department.id)
        .order_by(Department.name_key, Employee.name_key)
    )
    if not include_inactive:
        statement = statement.where(Employee.active.is_(True))
    if department_id is not None:
        statement = statement.where(Employee.department_id == department_id)

    with Session(models.engine) as session:
        rows = session.execute(statement).all()
    return [
        EmployeeInfo(
            id=i,
            name=n,
            department_id=d,
            department=dn,
            badge=b,
            phone_number=phone,
            id_card_number=card,
            active=bool(a),
        )
        for i, n, d, dn, b, phone, card, a in rows
    ]


def update_employee_details(employee_id, phone_number, id_card_number):
    phone, card_number = _validate_employee_details(phone_number, id_card_number)
    with Session(models.engine) as session:
        employee = session.get(Employee, employee_id)
        if employee is None:
            raise RuleError("Employee not found.")
        employee.phone_number = phone
        employee.id_card_number = card_number
        session.commit()


def change_employee_badge(employee_id, badge_number):
    badge = (badge_number or "").strip().upper()
    if not badge:
        raise RuleError("Please enter the employee's badge number.")

    with Session(models.engine) as session:
        employee = session.get(Employee, employee_id)
        if employee is None:
            raise RuleError("Employee not found.")

        if employee.badge_number == badge:
            return badge

        open_visit = session.scalar(
            select(Attendance.id).where(
                Attendance.employee_id == employee_id,
                Attendance.time_out.is_(None),
            )
        )
        if open_visit is not None:
            raise RuleError("This employee is on the platform right now.\nCheck them out first.")

        badge_owner = session.scalar(
            select(Employee).where(
                Employee.badge_number == badge,
                Employee.id != employee_id,
            )
        )
        if badge_owner is not None:
            raise RuleError(f"Badge {badge} already belongs to {badge_owner.name}.")

        former_owner_id = session.scalar(
            select(Attendance.employee_id)
            .where(
                Attendance.badge_number == badge,
                Attendance.employee_id != employee_id,
            )
            .limit(1)
        )
        if former_owner_id is not None:
            former_owner = session.get(Employee, former_owner_id)
            raise RuleError(f"Badge {badge} was previously used by {former_owner.name}.")

        employee.badge_number = badge
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise RuleError("This badge is already registered.")
    return badge


def get_open_employee_ids():
    # The ids of everyone who is on the platform right now
    with Session(models.engine) as session:
        ids = session.scalars(select(Attendance.employee_id).where(Attendance.time_out.is_(None))).all()
    return set(ids)


def deactivate_employee(employee_id):
    with Session(models.engine) as session:
        employee = session.get(Employee, employee_id)
        if employee is None:
            raise RuleError("Employee not found.")

        still_inside = session.scalar(
            select(Attendance.id).where(
                Attendance.employee_id == employee_id,
                Attendance.time_out.is_(None),
            )
        )
        if still_inside is not None:
            raise RuleError("This employee is on the platform right now.\nCheck them out first.")

        employee.active = False   # changing the object is enough ...
        session.commit()          # ... commit() writes the change to the database


def reactivate_employee(employee_id):
    with Session(models.engine) as session:
        employee = session.get(Employee, employee_id)
        if employee is None:
            raise RuleError("Employee not found.")
        employee.active = True
        session.commit()


# ---------------------------------------------------------------
# CHECK IN / CHECK OUT
# ---------------------------------------------------------------

def check_in(employee_id, now=None):
    # The employee hands over the ID card and receives THEIR badge.
    # Returns (name, badge, time_in).
    with Session(models.engine) as session:
        employee = session.get(Employee, employee_id)
        if employee is None:
            raise RuleError("Employee not found.")
        if not employee.active:
            raise RuleError(f"{employee.name} is deactivated.")
        if not employee.phone_number or employee.id_card_number is None:
            raise RuleError(
                f"Add {employee.name}'s phone number and ID card number in the Employees tab before check-in."
            )

        open_visit = session.scalar(
            select(Attendance).where(
                Attendance.employee_id == employee_id,
                Attendance.time_out.is_(None),
            )
        )
        if open_visit is not None:
            raise RuleError(
                f"{employee.name} is already on the platform "
                f"(since {open_visit.time_in:%Y-%m-%d %H:%M})."
            )

        name, badge = employee.name, employee.badge_number
        time_in = (now or datetime.now()).replace(microsecond=0)

        session.add(Attendance(employee_id=employee_id, badge_number=employee.badge_number, time_in=time_in))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise RuleError("This employee is already on the platform.")
    return name, badge, time_in


def check_out(attendance_id, now=None):
    # The employee gives the badge back and receives the ID card.
    # Returns (name, badge, time_out).
    with Session(models.engine) as session:
        visit = session.get(Attendance, attendance_id)
        if visit is None:
            raise RuleError("Record not found.")
        employee = session.get(Employee, visit.employee_id)

        if visit.time_out is not None:
            raise RuleError(f"{employee.name} already left at {visit.time_out:%Y-%m-%d %H:%M}.")

        time_out = (now or datetime.now()).replace(microsecond=0)
        if time_out < visit.time_in:
            raise RuleError(
                "The computer's clock shows a time earlier than the check-in time.\n"
                "Please correct the date and time of this computer."
            )

        name, badge = employee.name, visit.badge_number
        visit.time_out = time_out
        session.commit()
    return name, badge, time_out


# ---------------------------------------------------------------
# READING THE RECORDS
# ---------------------------------------------------------------

def _records_select():
    # The query shared by "today" and "search": one row per visit,
    # joined with the employee and the department
    return (
        select(
            Attendance.id,
            Employee.name,
            Department.name,
            Attendance.badge_number,
            Employee.phone_number,
            Employee.id_card_number,
            Attendance.time_in,
            Attendance.time_out,
        )
        .select_from(Attendance)
        .join(Employee, Attendance.employee_id == Employee.id)
        .join(Department, Employee.department_id == Department.id)
    )


def _to_records(rows):
    return [
        Record(
            id=i,
            employee=n,
            department=d,
            badge=b,
            phone_number=phone,
            id_card_number=card,
            time_in=ti,
            time_out=to,
        )
        for i, n, d, b, phone, card, ti, to in rows
    ]


def get_today_records(today=None):
    # Include visits started today, visits ended today, and anyone still on
    # the platform from an earlier day (for example someone on a night shift).
    today = today or date.today()
    start = datetime.combine(today, time.min)
    end = start + timedelta(days=1)

    statement = (
        _records_select()
        .where(
            or_(
                Attendance.time_out.is_(None),
                and_(Attendance.time_in >= start, Attendance.time_in < end),
                and_(Attendance.time_out >= start, Attendance.time_out < end),
            )
        )
        .order_by(Attendance.time_in)
    )
    with Session(models.engine) as session:
        rows = session.execute(statement).all()
    return _to_records(rows)


def search_records(date_from=None, date_to=None, department_id=None, name_text="", badge_text=""):
    # date_from / date_to are date objects (or None to ignore them).
    # Every other filter is ignored when left empty. Newest visits first.
    if date_from and date_to and date_from > date_to:
        raise RuleError("The 'From' date is later than the 'To' date.")

    statement = _records_select()

    if date_from:
        statement = statement.where(Attendance.time_in >= datetime.combine(date_from, time.min))
    if date_to:
        # up to (but not including) the start of the NEXT day, so all of date_to is included
        statement = statement.where(
            Attendance.time_in < datetime.combine(date_to + timedelta(days=1), time.min)
        )
    if department_id is not None:
        statement = statement.where(Employee.department_id == department_id)

    name_text = (name_text or "").strip()
    if name_text:
        # contains() = "the name includes this text"; autoescape makes a typed % or _ harmless
        statement = statement.where(Employee.name_key.contains(fold(name_text), autoescape=True))

    badge_text = (badge_text or "").strip().upper()
    if badge_text:
        statement = statement.where(Attendance.badge_number == badge_text)

    statement = statement.order_by(Attendance.time_in.desc())

    with Session(models.engine) as session:
        rows = session.execute(statement).all()
    return _to_records(rows)


# ---------------------------------------------------------------
# BACKUP
# ---------------------------------------------------------------

def backup_database(destination_path):
    if os.path.abspath(destination_path) == os.path.abspath(models.DB_PATH):
        raise RuleError("Choose a different file than the live database.")

    source = sqlite3.connect(models.DB_PATH)
    try:
        destination = sqlite3.connect(destination_path)
        try:
            source.backup(destination)   # SQLite's own safe way to copy a database
        finally:
            destination.close()
    except sqlite3.Error as error:
        raise RuleError(f"The backup failed: {error}")
    finally:
        source.close()
