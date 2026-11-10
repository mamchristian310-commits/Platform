"""
reports.py -- turns records into tables (pandas) and Excel files (openpyxl).

pandas builds the tables and the summaries.
openpyxl makes the Excel file look good (colours, widths, filters).
"""

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from common import format_duration

DETAIL_COLUMNS = [
    "Date",
    "Employee",
    "Structure",
    "Badge",
    "Phone number",
    "ID card number",
    "Time in",
    "Time out",
    "Duration",
    "Hours",
]


def records_to_dataframe(records):
    # One row per visit. "Hours" is a plain number so Excel can add it up.
    rows = []
    for record in records:
        duration = record.duration
        rows.append(
            {
                "Date": record.time_in.date(),
                "Employee": record.employee,
                "Structure": record.department,
                "Badge": record.badge,
                "Phone number": record.phone_number,
                "ID card number": record.id_card_number,
                "Time in": record.time_in,
                "Time out": record.time_out,
                "Duration": format_duration(duration),
                "Hours": round(duration.total_seconds() / 3600, 2) if duration is not None else None,
            }
        )
    return pd.DataFrame(rows, columns=DETAIL_COLUMNS)


def summary_by_department(detail):
    columns = ["Structure", "Visits", "Total hours"]
    if detail.empty:
        return pd.DataFrame(columns=columns)

    summary = detail.groupby("Structure").agg(
        Visits=("Employee", "size"),
        **{"Total hours": ("Hours", "sum")},   # visits still in progress have no hours yet
    )
    summary["Total hours"] = summary["Total hours"].round(2)
    return summary.reset_index()[columns]


def summary_by_employee(detail):
    columns = [
        "Structure",
        "Employee",
        "Badge",
        "Phone number",
        "ID card number",
        "Visits",
        "Total hours",
        "First visit",
        "Last visit",
    ]
    if detail.empty:
        return pd.DataFrame(columns=columns)

    summary = detail.groupby(
        ["Structure", "Employee", "Badge", "Phone number", "ID card number"],
        dropna=False,
    ).agg(
        Visits=("Date", "size"),
        **{
            "Total hours": ("Hours", "sum"),
            "First visit": ("Date", "min"),
            "Last visit": ("Date", "max"),
        },
    )
    summary["Total hours"] = summary["Total hours"].round(2)
    return summary.reset_index()[columns]


def style_sheet(sheet):
    # Header row: white bold text on a dark blue background
    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Number formats, found by the column's header text
    formats = {
        "Date": "yyyy-mm-dd",
        "First visit": "yyyy-mm-dd",
        "Last visit": "yyyy-mm-dd",
        "Time in": "yyyy-mm-dd hh:mm:ss",
        "Time out": "yyyy-mm-dd hh:mm:ss",
        "Hours": "0.00",
        "Total hours": "0.00",
    }
    for column in sheet.iter_cols(min_row=1, max_row=sheet.max_row):
        number_format = formats.get(column[0].value)
        if number_format:
            for cell in column[1:]:
                cell.number_format = number_format

    # Column widths that fit the longest text in each column
    for column in sheet.iter_cols(min_row=1, max_row=sheet.max_row):
        longest = max((len(str(cell.value)) for cell in column if cell.value is not None), default=8)
        sheet.column_dimensions[get_column_letter(column[0].column)].width = min(max(longest + 3, 12), 45)

    sheet.freeze_panes = "A2"                  # the header stays visible when scrolling
    sheet.auto_filter.ref = sheet.dimensions   # filter arrows on every column


def export_to_excel(records, path):
    detail = records_to_dataframe(records)
    by_department = summary_by_department(detail)
    by_employee = summary_by_employee(detail)

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        detail.to_excel(writer, sheet_name="Records", index=False)
        by_department.to_excel(writer, sheet_name="By structure", index=False)
        by_employee.to_excel(writer, sheet_name="By employee", index=False)
        for sheet in writer.book.worksheets:
            style_sheet(sheet)
