"""
models.py -- the database tables, written as Python classes (this is the "ORM").

  A class      = a table
  An attribute = a column
  An object    = one row of that table

SQLAlchemy turns these classes into real SQLite tables and writes the SQL for us.
"""

import os
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, create_engine, event, inspect, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# The data lives in the user's own folder (not next to the program), so that
#  - the folder is always writable
#  - replacing or updating the program never touches the records
DATA_FOLDER = os.path.join(os.path.expanduser("~"), "PlatformLog")
DB_PATH = os.path.join(DATA_FOLDER, "platform_log.db")

engine = None  # created by init_db()


class Base(DeclarativeBase):
    pass


class Department(Base):
    __tablename__ = "department"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    # name_key is the name without capitals or accents ("Éducation" -> "education").
    # It is what we compare and sort by, so "Education" and "éducation"
    # can never both exist.
    name_key: Mapped[str] = mapped_column(String(100), unique=True)


class Employee(Base):
    __tablename__ = "employee"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    name_key: Mapped[str] = mapped_column(String(150))
    department_id: Mapped[int] = mapped_column(ForeignKey("department.id"))

    # unique=True: a badge belongs to ONE employee and can never be shared.
    # A deactivated employee keeps their badge, so it can't be given to anyone else.
    badge_number: Mapped[str] = mapped_column(String(50), unique=True)
    phone_number: Mapped[Optional[str]] = mapped_column(String(9), nullable=True)
    id_card_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Employees are never deleted, only deactivated, so old records keep their meaning
    active: Mapped[bool] = mapped_column(default=True)

    __table_args__ = (UniqueConstraint("name_key", "department_id"),)


class Attendance(Base):
    __tablename__ = "attendance"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id"))
    badge_number: Mapped[str] = mapped_column(String(50))
    time_in: Mapped[datetime] = mapped_column(DateTime, index=True)
    # time_out stays empty (None) until the employee gets their ID card back
    time_out: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


@event.listens_for(Engine, "connect")
def turn_on_foreign_keys(dbapi_connection, connection_record):
    # SQLite ignores foreign keys unless we ask for them on every connection
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db(db_path=None):
    # Creates the database file and the tables if they don't exist yet.
    # Safe to call every time the program starts.
    global engine, DB_PATH
    if db_path is not None:
        DB_PATH = db_path

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    engine = create_engine(URL.create("sqlite", database=DB_PATH))
    Base.metadata.create_all(engine)

    with engine.begin() as connection:
        attendance_columns = {
            column["name"] for column in inspect(connection).get_columns("attendance")
        }
        if "badge_number" not in attendance_columns:
            connection.execute(text("ALTER TABLE attendance ADD COLUMN badge_number VARCHAR(50)"))
        connection.execute(
            text(
                "UPDATE attendance "
                "SET badge_number = (SELECT employee.badge_number "
                "FROM employee WHERE employee.id = attendance.employee_id) "
                "WHERE badge_number IS NULL"
            )
        )

        employee_columns = {
            column["name"] for column in inspect(connection).get_columns("employee")
        }
        if "phone_number" not in employee_columns:
            connection.execute(text("ALTER TABLE employee ADD COLUMN phone_number VARCHAR(16)"))
        if "id_card_number" not in employee_columns:
            connection.execute(text("ALTER TABLE employee ADD COLUMN id_card_number VARCHAR(50)"))

        # Safety net inside the database itself: even if the program had a bug,
        # SQLite refuses to record a person as being on the platform twice.
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS one_open_visit_per_employee "
                "ON attendance(employee_id) WHERE time_out IS NULL"
            )
        )

    id_card_column = next(
        column
        for column in inspect(engine).get_columns("employee")
        if column["name"] == "id_card_number"
    )
    if not isinstance(id_card_column["type"], String):
        _migrate_id_card_number_to_text()


def _migrate_id_card_number_to_text():
    raw_connection = engine.raw_connection()
    try:
        cursor = raw_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute("BEGIN")
        try:
            cursor.execute(
                """
                CREATE TABLE employee_new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    name VARCHAR(150) NOT NULL,
                    name_key VARCHAR(150) NOT NULL,
                    department_id INTEGER NOT NULL REFERENCES department (id),
                    badge_number VARCHAR(50) NOT NULL UNIQUE,
                    phone_number VARCHAR(9),
                    id_card_number VARCHAR(50),
                    active BOOLEAN NOT NULL,
                    UNIQUE (name_key, department_id)
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO employee_new
                    (id, name, name_key, department_id, badge_number, phone_number,
                     id_card_number, active)
                SELECT id, name, name_key, department_id, badge_number, phone_number,
                       CAST(id_card_number AS TEXT), active
                FROM employee
                """
            )
            cursor.execute("DROP TABLE employee")
            cursor.execute("ALTER TABLE employee_new RENAME TO employee")
            raw_connection.commit()
        except Exception:
            raw_connection.rollback()
            raise
        finally:
            cursor.execute("PRAGMA foreign_keys=ON")

        violations = cursor.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Foreign key violations after ID card migration: {violations}")
    finally:
        raw_connection.close()
