"""
common.py -- small pieces shared by every other file.
Nothing in here needs a database or a window, so it is easy to test.
"""

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional


class RuleError(Exception):
    """A rule of the platform was broken.
    The message is written to be shown directly to the person using the app."""


def fold(text):
    # Makes names easy to compare and search: "ERIC", "eric" and "Éric"
    # all become "eric". It is used for duplicate checks and for searching.
    decomposed = unicodedata.normalize("NFKD", (text or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def clean_name(text):
    # Removes extra spaces: "  Alice   Ngu " -> "Alice Ngu"
    return " ".join((text or "").split())


@dataclass
class EmployeeInfo:
    id: int
    name: str
    department_id: int
    department: str
    badge: str
    active: bool


@dataclass
class Record:
    # One visit to the platform. time_out is None while the employee
    # is still on the platform (they have not got their ID card back yet).
    id: int
    employee: str
    department: str
    badge: str
    time_in: datetime
    time_out: Optional[datetime]

    @property
    def duration(self) -> Optional[timedelta]:
        if self.time_out is None:
            return None
        return self.time_out - self.time_in


def format_duration(duration):
    if duration is None:
        return "On platform"
    total_minutes = int(duration.total_seconds()) // 60
    return f"{total_minutes // 60}h {total_minutes % 60:02d}m"


def format_time(value, reference_date):
    # "08:03:12" if it happened on reference_date, otherwise the full date
    # and time (so a night shift that ends the next day is clear)
    if value is None:
        return ""
    if value.date() == reference_date:
        return value.strftime("%H:%M:%S")
    return value.strftime("%Y-%m-%d %H:%M:%S")
