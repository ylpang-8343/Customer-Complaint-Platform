"""The complaint log as an Excel file, in the columns of the current log (proposal section 5.5)."""

from io import BytesIO

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import reporting

DATE_FORMAT = "dd mmm yyyy"
DATETIME_FORMAT = "dd mmm yyyy hh:mm"
HEADER_FILL = PatternFill("solid", start_color="7A4A10")
HEADER_FONT = Font(bold=True, color="FFFFFF")
THIN = Side(style="thin", color="D9D0BF")
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def _naive(value):
    """Excel has no time zones: write the local time."""
    if hasattr(value, "tzinfo") and value.tzinfo is not None:
        return timezone.localtime(value).replace(tzinfo=None)
    return value


def _put(sheet, row, column, value, kind="text"):
    """Write one value. Text that Excel would run as a formula is stored as plain text instead."""
    cell = sheet.cell(row=row, column=column)
    value = _naive(value)
    if value is None or value == "":
        return cell
    cell.value = value
    if isinstance(value, str):
        if value.startswith(FORMULA_STARTS):
            cell.data_type = "s"  # a buyer called "=1+1" must stay a name, not become a formula
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    elif kind == "datetime":
        cell.number_format = DATETIME_FORMAT
        cell.alignment = Alignment(horizontal="left", vertical="top")
    elif kind == "date":
        cell.number_format = DATE_FORMAT
        cell.alignment = Alignment(horizontal="left", vertical="top")
    return cell


def build_workbook(queryset, user, factories=None, date_from=None, date_to=None):
    """Return the .xlsx file as bytes: the log, then the summary under it, then who exported what."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Complaint log"

    headers = ["No."] + [header for header, _, _, _ in reporting.LOG_COLUMNS]
    for column, header in enumerate(headers, start=1):
        cell = sheet.cell(row=1, column=column, value=header)
        cell.font, cell.fill = HEADER_FONT, HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    sheet.row_dimensions[1].height = 32
    sheet.column_dimensions["A"].width = 6
    for column, (_, _, width, _) in enumerate(reporting.LOG_COLUMNS, start=2):
        sheet.column_dimensions[get_column_letter(column)].width = width

    complaints = list(reporting.log_queryset(queryset))
    for number, complaint in enumerate(complaints, start=1):
        row = number + 1
        _put(sheet, row, 1, number, "number").alignment = Alignment(horizontal="center", vertical="top")
        for column, ((_, kind, _, getter)) in enumerate(reporting.LOG_COLUMNS, start=2):
            _put(sheet, row, column, getter(complaint), kind)
        for column in range(1, len(headers) + 1):
            sheet.cell(row=row, column=column).border = Border(bottom=THIN)

    last = len(complaints) + 1
    sheet.freeze_panes = "C2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(last, 1)}"

    # The summary the current log carries under the table (Figure 11)
    row = last + 2
    figures = reporting.summary(queryset)
    sheet.cell(row=row, column=2, value="Summary").font = Font(bold=True, size=12)
    for key, label in reporting.SUMMARY_LABELS.items():
        row += 1
        sheet.cell(row=row, column=2, value=label)
        sheet.cell(row=row, column=3, value=figures[key]).alignment = Alignment(horizontal="left")
    row += 2
    sheet.cell(row=row, column=2, value="Cases for each type of valid complaint").font = Font(bold=True)
    for name, count in reporting.valid_by_type(queryset):
        row += 1
        _put(sheet, row, 2, name)
        sheet.cell(row=row, column=3, value=count).alignment = Alignment(horizontal="left")
    row += 2
    exporter = user.get_full_name() or user.get_username()
    note = f"Exported by {exporter} on {timezone.localtime():%d %b %Y, %H:%M}. {reporting.period_text(factories, date_from, date_to)}."
    _put(sheet, row, 2, note).alignment = Alignment(wrap_text=False)

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
