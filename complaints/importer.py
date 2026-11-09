"""Load the past complaints into the platform from one spreadsheet (proposal section 5.4 "Past complaints", open point 2).

The Coordinator downloads the template, fills one row for each past complaint and uploads it. The file is
first checked row by row and nothing is saved until it is clean and the Coordinator confirms; then every row
becomes a complaint, all together or not at all.

What an old complaint becomes
  * It gets a platform reference (FA/2026/0007) in date order, so the running numbers carry on from it. The number it
    had in the old log is kept in "Number in the previous log".
  * Its stage is worked out from the dates: a date case closed makes it Closed, a reply date makes it
    Customer Response Sent, any investigation or decision makes it PIC investigation, otherwise Submitted.
    A complaint that is still open starts its reminder clock at the import, not in the past.
  * Fields the old log did not have stay blank. No emails are sent.

The columns are defined once, in ``COLUMNS``, and drive the template, the checks and the Help text.
"""

import datetime
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.datetime import from_excel
from openpyxl.worksheet.datavalidation import DataValidation

from accounts.models import Factory
from .models import (
    ComplaintMethod,
    ComplaintSubType,
    ComplaintType,
    Complaint,
    ComplaintEvent,
    CustomerReply,
    ImportBatch,
    Investigation,
    LabSection,
    Verification,
)

STATUS = Complaint.Status
MAX_ROWS = 2000
MAX_FILE_MB = 5
SHEET_NAME = "Complaints"
SIX_MONTHS = datetime.timedelta(days=183)
DEFAULT_HOUR = datetime.time(9, 0)  # a date without a time is taken as 09:00


class ImportProblem(Exception):
    """The import cannot go ahead (the file has errors, or it was already imported)."""


@dataclass(frozen=True)
class Column:
    key: str
    header: str
    kind: str  # text, longtext, date, datetime, decimal, yesno, choice, email
    required: bool = False
    help: str = ""
    example: str = ""
    width: int = 18
    max_length: int | None = None
    choices: str = ""  # the name of the list in LISTS, for kind "choice"


COLUMNS = [
    Column("factory", "Factory", "choice", True, "The factory the complaint was lodged for: code (FA) or name (Factory A).", "FA", 12, choices="factory"),
    Column("legacy_reference", "Complaint no. in the old log", "text", False, "The number it had before the platform. Kept for tracing. Must not be used twice for a factory.", "FA-043", 16, 60),
    Column("lodger", "Complaint received by", "text", False, "Name of the person who received it, as in the old log.", "A. Tan", 20, 100),
    Column("issue_date", "Issue date", "datetime", True, "When the complaint was lodged. A date alone is taken as 09:00. Not in the future.", "2026-08-14", 16),
    Column("buyer", "Complaint from", "text", True, "Buyer or party involved.", "Example Buyer Ltd", 26, 200),
    Column("contract_number", "Contract number", "text", False, "", "SC-000123", 16, 100),
    Column("product_name", "Product", "text", False, "", "Refined oil", 20, 200),
    Column("delivery_date", "Delivery date", "date", False, "", "2026-08-01", 14),
    Column("packing_type", "Packing type", "choice", False, "Bulk or Pack.", "Pack", 12, choices="packing"),
    Column("pack_size", "Pack size", "text", False, "For Pack: drum, flexibag, carton...", "Flexibag", 14, 100),
    Column("affected_quantity", "Affected quantity", "decimal", False, "A number, with the unit in the next column.", "20", 12),
    Column("quantity_unit", "Unit", "choice", False, "Metric Ton or Kg.", "Metric Ton", 12, choices="unit"),
    Column("transport_type", "Transport type", "choice", False, "One of the four transport types.", "Shipment - Container", 22, choices="transport"),
    Column("vessel_name", "Vessel name", "text", False, "", "MV Example", 16, 100),
    Column("vehicle_number", "Vehicle number", "text", False, "", "ABC 1234", 14, 50),
    Column("container_number", "Container number", "text", False, "", "ABCD1234567", 16, 50),
    Column("loading_port", "Loading port", "text", False, "", "Example Loading Port", 20, 100),
    Column("discharge_port", "Port of discharge / final destination", "text", False, "", "Example Discharge Port", 22, 100),
    Column("description", "Description of complaint", "longtext", False, "", "Flexibag leaked during discharge.", 40),
    Column("complaint_type", "Complaint type", "choice", True, "One of the complaint types the Coordinator maintains in Settings.", "Packaging", 18, choices="type"),
    Column("complaint_sub_type", "Sub-type", "choice", False, "A sub-type of that complaint type.", "Packaging - Leaking", 24, choices="subtype"),
    Column("type_details", "Short details", "text", False, "For the type or sub-type Others.", "", 20, 200),
    Column("sample_sent", "Sample sent to factory", "yesno", False, "Yes or No.", "Yes", 12, choices="yesno"),
    Column("do_reference", "DO / document reference", "text", False, "", "DO-5521", 16, 100),
    Column("contact_person", "Contact person", "text", False, "At the buyer.", "Sample Contact", 18, 100),
    Column("contact_email", "Email", "email", False, "The buyer's email address.", "contact@example.com", 24),
    Column("contact_phone", "Telephone", "text", False, "", "", 14, 50),
    Column("contact_fax", "Fax", "text", False, "", "", 14, 50),
    Column("complaint_method", "Complaint method", "choice", False, "How it was received: one of the methods the Coordinator maintains.", "Email", 16, choices="method"),
    Column("lab_date", "Lab acknowledgement date", "date", False, "The date the laboratory received the sample.", "2026-08-16", 16),
    Column("ims_date", "IMS acknowledgement date", "datetime", False, "The date the Coordinator (IMS) acknowledged it.", "2026-08-15", 16),
    Column("ims_remarks", "IMS remarks", "longtext", False, "", "", 30),
    Column("root_cause", "Root cause", "longtext", False, "The PIC's root cause analysis, if the old log has it.", "", 30),
    Column("correction", "Correction", "longtext", False, "", "Replaced the flexibags.", 30),
    Column("corrective_action", "Corrective action", "longtext", False, "", "Audit the supplier.", 30),
    Column("reply_date", "Date reply to buyer / trader", "date", False, "The date the reply was sent. Needs Valid or invalid. Makes the stage Customer Response Sent.", "2026-08-25", 16),
    Column("hod_decision", "Valid or invalid", "choice", False, "Valid or Invalid, as the HOD decided. Needed when there is a reply date or a closing date.", "Valid", 12, choices="decision"),
    Column("hod_reason", "Reason if invalid", "longtext", False, "", "", 26),
    Column("first_verified", "First verified date", "date", False, "", "2026-09-10", 16),
    Column("second_verified", "Second verified date", "date", False, "", "", 16),
    Column("closed_date", "Date case closed", "date", False, "Makes the stage Closed. Needs Valid or invalid.", "2026-09-12", 16),
    Column("remarks", "Remarks", "longtext", False, "Final remarks or the closing comment.", "Effective.", 26),
    Column("deleted", "Deleted", "yesno", False, "Yes if the complaint was deleted in the old log.", "", 10, choices="yesno"),
    Column("deleted_reason", "Reason delete", "longtext", False, "Needed when Deleted is Yes.", "", 26),
]
BY_KEY = {column.key: column for column in COLUMNS}
GROUPS = [("Identification", "factory", "issue_date"), ("The complaint", "buyer", "complaint_method"), ("Handling", "lab_date", "deleted_reason")]

PACKING = OrderedDict([("bulk", "Bulk"), ("pack", "Pack")])
UNITS = OrderedDict([("mt", "Metric Ton"), ("kg", "Kg")])
UNIT_ALIASES = {"mt": "mt", "metric ton": "mt", "metric tonne": "mt", "tonne": "mt", "ton": "mt", "kg": "kg", "kgs": "kg", "kilogram": "kg"}
ALIASES = {"customer complaint no.": "legacy_reference", "customer complaint no": "legacy_reference", "no. in the old log": "legacy_reference"}
OUTCOME_LABEL = {
    STATUS.SUBMITTED: "Submitted",
    STATUS.INVESTIGATION: "PIC investigation",
    STATUS.RESPONSE_SENT: "Customer Response Sent",
    STATUS.CLOSED: "Closed",
}


def _norm(text):
    return re.sub(r"\s+", " ", str(text or "").strip().strip("*").strip()).lower()


# --- The lists a choice column accepts ----------------------------------------------------------------------------


def lists():
    """The allowed values for each choice column, as they appear in the template's Lists sheet."""
    transport = [label for _, label in Complaint.TransportType.choices]
    return OrderedDict(
        factory=[f"{f.code}" for f in Factory.objects.all()],
        packing=list(PACKING.values()),
        unit=list(UNITS.values()),
        transport=transport,
        type=[t.name for t in ComplaintType.objects.filter(is_active=True)],
        subtype=[s.name for s in ComplaintSubType.objects.filter(is_active=True)],
        yesno=["Yes", "No"],
        method=[m.name for m in ComplaintMethod.objects.filter(is_active=True)],
        decision=["Valid", "Invalid"],
    )


LIST_TITLES = {
    "factory": "Factory", "packing": "Packing", "unit": "Unit", "transport": "Transport", "type": "Type",
    "subtype": "Sub-type", "yesno": "Yes/No", "method": "Method", "decision": "Decision",
}  # fmt: skip


# --- The template ---------------------------------------------------------------------------------------------------


def build_template():
    """The .xlsx the Coordinator fills in: the sheet, the instructions and the lists, with drop-downs."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_NAME
    options = lists()

    required_fill = PatternFill("solid", start_color="7A4A10")
    optional_fill = PatternFill("solid", start_color="F7E9D2")
    for number, column in enumerate(COLUMNS, start=1):
        cell = sheet.cell(row=1, column=number, value=column.header)
        cell.fill = required_fill if column.required else optional_fill
        cell.font = Font(bold=True, color="FFFFFF" if column.required else "7A4A10")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        sheet.column_dimensions[get_column_letter(number)].width = max(column.width, 12)
        if column.kind in ("date", "datetime"):
            for row in range(2, MAX_ROWS + 2):
                sheet.cell(row=row, column=number).number_format = "dd mmm yyyy"
    sheet.row_dimensions[1].height = 34
    sheet.freeze_panes = "B2"

    # Lists sheet, and a drop-down on each choice column
    lists_sheet = workbook.create_sheet("Lists")
    letters = {}
    for number, (name, values) in enumerate(options.items(), start=1):
        letters[name] = get_column_letter(number)
        lists_sheet.cell(row=1, column=number, value=LIST_TITLES[name]).font = Font(bold=True)
        for row, value in enumerate(values, start=2):
            lists_sheet.cell(row=row, column=number, value=value)
        lists_sheet.column_dimensions[get_column_letter(number)].width = 26
    for number, column in enumerate(COLUMNS, start=1):
        if column.kind in ("choice", "yesno") and column.choices in letters and options[column.choices]:
            letter, size = letters[column.choices], len(options[column.choices]) + 1
            validation = DataValidation(type="list", formula1=f"=Lists!${letter}$2:${letter}${size}", allow_blank=True)
            validation.error, validation.errorTitle = "Choose a value from the list (see the Lists sheet).", "Not in the list"
            sheet.add_data_validation(validation)
            validation.add(f"{get_column_letter(number)}2:{get_column_letter(number)}{MAX_ROWS + 1}")

    # Instructions sheet
    guide = workbook.create_sheet("Instructions", 0)
    guide.column_dimensions["A"].width = 34
    guide.column_dimensions["B"].width = 11
    guide.column_dimensions["C"].width = 70
    guide.column_dimensions["D"].width = 28
    lines = [
        ("Upload of past complaints", True),
        ("Fill in one row in the sheet 'Complaints' for each past complaint (up to %d), keep the first row as it is, and upload the file under Import in the platform." % MAX_ROWS, False),
        ("The file is checked first. Nothing is saved until every row is correct and the Coordinator confirms.", False),
        ("Each complaint gets a platform reference in date order. Its stage comes from the dates: a closing date makes it Closed, a reply date makes it Customer Response Sent, any investigation or decision makes it PIC investigation, otherwise Submitted.", False),
        ("Leave a cell empty if the old log did not have it. Only the dark columns must be filled in. Dates can be typed as 2026-08-14 or 14/08/2026. No emails are sent for imported complaints.", False),
        ("", False),
    ]
    for row, (text, bold) in enumerate(lines, start=1):
        cell = guide.cell(row=row, column=1, value=text)
        cell.font = Font(bold=bold, size=14 if bold else 11)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        guide.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
        if text and not bold:
            guide.row_dimensions[row].height = 32
    header_row = len(lines) + 1
    for number, title in enumerate(("Column", "Required", "What to enter", "Example"), start=1):
        cell = guide.cell(row=header_row, column=number, value=title)
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), required_fill
    for offset, column in enumerate(COLUMNS, start=1):
        row = header_row + offset
        allowed = options.get(column.choices) if column.choices else None
        detail = column.help + (f" Choose from: {', '.join(allowed)}." if allowed and column.choices not in ("subtype",) else "")
        for number, value in enumerate((column.header, "Yes" if column.required else "", detail.strip(), column.example), start=1):
            cell = guide.cell(row=row, column=number, value=value)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# --- Reading and checking a file ---------------------------------------------------------------------------------------


@dataclass
class RowResult:
    row: int
    values: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)  # (column header, message)
    warnings: list = field(default_factory=list)  # messages

    @property
    def ok(self):
        return not self.errors

    @property
    def stage_label(self):
        return OUTCOME_LABEL.get(self.values.get("status"), "")


@dataclass
class Analysis:
    rows: list = field(default_factory=list)
    file_errors: list = field(default_factory=list)
    file_warnings: list = field(default_factory=list)

    @property
    def error_count(self):
        return len(self.file_errors) + sum(len(r.errors) for r in self.rows)

    @property
    def rows_with_errors(self):
        return sum(1 for r in self.rows if r.errors)

    @property
    def can_import(self):
        return bool(self.rows) and self.error_count == 0


DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%Y/%m/%d")


def _parse_date(value):
    """A datetime, or None when it cannot be read. Dates without a time have no time part set."""
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.date):
        return datetime.datetime.combine(value, datetime.time())
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 20000 < value < 80000:
        return from_excel(value)
    if isinstance(value, str):
        text = value.strip()
        for fmt in DATE_FORMATS + ("%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.datetime.strptime(text, fmt)
            except ValueError:
                continue
    return None


def _aware(value):
    """Make a datetime that has no time zone local; a date alone becomes 09:00."""
    if value.time() == datetime.time(0, 0):
        value = datetime.datetime.combine(value.date(), DEFAULT_HOUR)
    return timezone.make_aware(value) if timezone.is_naive(value) else value


class Lookups:
    """The reference data a row is checked against, read once for the whole file."""

    def __init__(self):
        self.factories = {}
        for f in Factory.objects.all():
            self.factories[_norm(f.code)] = f
            self.factories[_norm(f.name)] = f
        self.types = {_norm(t.name): t for t in ComplaintType.objects.all()}
        self.sub_types = {(s.complaint_type_id, _norm(s.name)): s for s in ComplaintSubType.objects.all()}
        self.methods = {_norm(m.name): m for m in ComplaintMethod.objects.all()}
        self.transport = {_norm(label): value for value, label in Complaint.TransportType.choices}
        self.packing = {_norm(label): value for value, label in PACKING.items()} | {_norm(k): k for k in PACKING}
        self.existing_legacy = {
            (factory_id, _norm(ref)) for factory_id, ref in Complaint.objects.exclude(legacy_reference="").values_list("factory_id", "legacy_reference")
        }


def _read_rows(handle):
    """(header map, rows) from the first sheet called Complaints, or the first sheet."""
    workbook = load_workbook(handle, read_only=True, data_only=True)
    sheet = workbook[SHEET_NAME] if SHEET_NAME in workbook.sheetnames else workbook.worksheets[0]
    iterator = sheet.iter_rows(values_only=True)
    try:
        headers = next(iterator)
    except StopIteration:
        return {}, [], []
    mapping, unknown = {}, []
    by_header = {_norm(c.header): c.key for c in COLUMNS} | ALIASES
    for index, header in enumerate(headers):
        if header in (None, ""):
            continue
        key = by_header.get(_norm(header))
        if key and key not in mapping.values():
            mapping[index] = key
        else:
            unknown.append(str(header))
    rows = []
    for number, values in enumerate(iterator, start=2):
        if all(v in (None, "") or (isinstance(v, str) and not v.strip()) for v in values):
            continue
        rows.append((number, {key: values[index] if index < len(values) else None for index, key in mapping.items()}))
    return mapping, rows, unknown


def analyse(handle):
    """Check a spreadsheet. Nothing is saved; the result says what is wrong and what each row would become."""
    result = Analysis()
    try:
        mapping, rows, unknown = _read_rows(handle)
    except Exception:  # not an Excel file, damaged, or a password-protected one
        result.file_errors.append("The file could not be read as an Excel (.xlsx) workbook.")
        return result
    missing = [c.header for c in COLUMNS if c.required and c.key not in mapping.values()]
    if missing:
        result.file_errors.append("The first row must have the template's headers. Missing: " + ", ".join(missing) + ".")
        return result
    if unknown:
        result.file_warnings.append("These columns are not in the template and are ignored: " + ", ".join(unknown) + ".")
    if not rows:
        result.file_errors.append("The sheet has no complaints: fill in at least one row under the headers.")
        return result
    if len(rows) > MAX_ROWS:
        result.file_errors.append(f"The sheet has {len(rows)} rows; upload at most {MAX_ROWS} at a time.")
        return result

    lookups, seen, today = Lookups(), set(), timezone.localdate()
    for number, raw in rows:
        result.rows.append(_check_row(number, raw, lookups, seen, today))
    return result


def _check_row(number, raw, lookups, seen, today):
    row = RowResult(row=number)
    values = row.values

    def error(key, message):
        row.errors.append((BY_KEY[key].header, message))

    def text(key):
        value = raw.get(key)
        if value is None:
            return ""
        if isinstance(value, float) and value.is_integer():
            value = int(value)  # an Excel number such as a contract number 5521
        value = str(value).strip()
        column = BY_KEY[key]
        if column.max_length and len(value) > column.max_length:
            error(key, f"Too long ({len(value)} characters; at most {column.max_length}).")
            value = value[: column.max_length]
        return value

    def date(key, required=False, future_ok=False):
        value = raw.get(key)
        if value in (None, "") or (isinstance(value, str) and not value.strip()):
            if required:
                error(key, "This is needed.")
            return None
        parsed = _parse_date(value)
        if parsed is None:
            error(key, f"'{value}' is not a date. Use 2026-08-14 or 14/08/2026.")
            return None
        if not future_ok and parsed.date() > today:
            error(key, "The date is in the future.")
        return parsed

    def yesno(key):
        value = _norm(raw.get(key))
        if not value:
            return None
        if value in ("yes", "y", "true", "1"):
            return True
        if value in ("no", "n", "false", "0"):
            return False
        error(key, f"'{raw.get(key)}' is not Yes or No.")
        return None

    # Factory and the old number
    factory = None
    name = _norm(raw.get("factory"))
    if not name:
        error("factory", "This is needed.")
    elif name not in lookups.factories:
        error("factory", f"'{raw.get('factory')}' is not a factory. Use a code such as FA, or a name such as Factory A.")
    else:
        factory = lookups.factories[name]
    values["factory"] = factory
    legacy = text("legacy_reference")
    values["legacy_reference"] = legacy
    if legacy and factory:
        key = (factory.pk, _norm(legacy))
        if key in lookups.existing_legacy:
            error("legacy_reference", f"'{legacy}' was already imported for {factory.name}.")
        elif key in seen:
            error("legacy_reference", f"'{legacy}' appears twice for {factory.name} in this file.")
        seen.add(key)

    values["lodger"] = text("lodger")
    issue = date("issue_date", required=True)
    values["issue_at"] = _aware(issue) if issue else None
    if issue and issue.date() < today - SIX_MONTHS:
        row.warnings.append("The issue date is more than 6 months ago.")
    values["buyer"] = text("buyer")
    if not values["buyer"]:
        error("buyer", "This is needed.")
    for key in ("contract_number", "product_name", "pack_size", "vessel_name", "vehicle_number", "container_number",
                "loading_port", "discharge_port", "type_details", "do_reference", "contact_person", "contact_phone", "contact_fax"):  # fmt: skip
        values[key] = text(key)
    values["description"] = text("description")
    delivered = date("delivery_date")
    values["delivery_date"] = delivered.date() if delivered else None

    packing = _norm(raw.get("packing_type"))
    values["packing_type"] = ""
    if packing:
        if packing in lookups.packing:
            values["packing_type"] = lookups.packing[packing]
        else:
            error("packing_type", f"'{raw.get('packing_type')}' is not Bulk or Pack.")
    quantity = raw.get("affected_quantity")
    values["affected_quantity"] = None
    if quantity not in (None, ""):
        try:
            amount = Decimal(str(quantity).strip().replace(",", ""))
            if amount < 0 or amount >= Decimal("1e9"):
                raise InvalidOperation
            values["affected_quantity"] = round(amount, 3)
        except InvalidOperation:
            error("affected_quantity", f"'{quantity}' is not a number (0 or more).")
    unit = _norm(raw.get("quantity_unit"))
    values["quantity_unit"] = ""
    if unit:
        if unit in UNIT_ALIASES:
            values["quantity_unit"] = UNIT_ALIASES[unit]
        else:
            error("quantity_unit", f"'{raw.get('quantity_unit')}' is not Metric Ton or Kg.")
    transport = _norm(raw.get("transport_type"))
    values["transport_type"] = ""
    if transport:
        if transport in lookups.transport:
            values["transport_type"] = lookups.transport[transport]
        else:
            error("transport_type", f"'{raw.get('transport_type')}' is not one of the four transport types.")

    # Type and sub-type
    ctype = None
    type_name = _norm(raw.get("complaint_type"))
    if not type_name:
        error("complaint_type", "This is needed.")
    elif type_name not in lookups.types:
        error("complaint_type", f"'{raw.get('complaint_type')}' is not a complaint type. See the Lists sheet.")
    else:
        ctype = lookups.types[type_name]
    values["complaint_type"] = ctype
    values["complaint_sub_type"] = None
    sub_name = _norm(raw.get("complaint_sub_type"))
    if sub_name:
        if ctype is None:
            error("complaint_sub_type", "Give a valid complaint type first.")
        elif (ctype.pk, sub_name) in lookups.sub_types:
            values["complaint_sub_type"] = lookups.sub_types[(ctype.pk, sub_name)]
        else:
            error("complaint_sub_type", f"'{raw.get('complaint_sub_type')}' is not a sub-type of {ctype.name}.")

    values["sample_sent"] = yesno("sample_sent")
    email = text("contact_email")
    values["contact_email"] = email
    if email:
        try:
            validate_email(email)
        except ValidationError:
            error("contact_email", f"'{email}' is not an email address.")
    method = _norm(raw.get("complaint_method"))
    values["complaint_method"] = None
    if method:
        if method in lookups.methods:
            values["complaint_method"] = lookups.methods[method]
        else:
            error("complaint_method", f"'{raw.get('complaint_method')}' is not a complaint method. See the Lists sheet.")

    # Handling
    lab = date("lab_date")
    ims = date("ims_date")
    reply = date("reply_date")
    first, second, closed = date("first_verified"), date("second_verified"), date("closed_date")
    for key, value in (("lab_date", lab), ("reply_date", reply), ("closed_date", closed)):
        values[key] = value.date() if value else None
    values["ims_at"] = _aware(ims) if ims else None
    values["first_verified"] = first.date() if first else None
    values["second_verified"] = second.date() if second else None
    for key in ("ims_remarks", "root_cause", "correction", "corrective_action", "hod_reason", "remarks", "deleted_reason"):
        values[key] = text(key)

    decision = _norm(raw.get("hod_decision"))
    values["hod_decision"] = ""
    if decision:
        if decision in ("valid", "invalid"):
            values["hod_decision"] = decision
        else:
            error("hod_decision", f"'{raw.get('hod_decision')}' is not Valid or Invalid.")
    deleted = yesno("deleted")
    values["deleted"] = bool(deleted)
    if deleted and not values["deleted_reason"]:
        error("deleted_reason", "Give the reason for deleting when Deleted is Yes.")
    if values["hod_decision"] == "invalid" and not values["hod_reason"]:
        row.warnings.append("The complaint is invalid but no reason is given.")

    # The stage follows from the dates
    if values["closed_date"]:
        values["status"] = STATUS.CLOSED
    elif values["reply_date"]:
        values["status"] = STATUS.RESPONSE_SENT
    elif any(values[k] for k in ("lab_date", "ims_at", "root_cause", "correction", "corrective_action", "hod_decision")):
        values["status"] = STATUS.INVESTIGATION
    else:
        values["status"] = STATUS.SUBMITTED
    if values["status"] in (STATUS.CLOSED, STATUS.RESPONSE_SENT) and not values["hod_decision"]:
        error("hod_decision", "Say whether the complaint is Valid or Invalid: it is needed when there is a reply date or a closing date.")

    # Things that look wrong but may be true of an old record
    if values["delivery_date"] and values["issue_at"] and values["delivery_date"] > values["issue_at"].date():
        row.warnings.append("The delivery date is after the issue date.")
    if values["reply_date"] and values["issue_at"] and values["reply_date"] < values["issue_at"].date():
        row.warnings.append("The reply date is before the issue date.")
    if values["closed_date"] and values["reply_date"] and values["closed_date"] < values["reply_date"]:
        row.warnings.append("The case was closed before the reply date.")
    if values["lab_date"] and values["sample_sent"] is False:
        row.warnings.append("There is a lab date but Sample sent is No.")
    return row


# --- Importing -------------------------------------------------------------------------------------------------------------


def _local_datetime(day):
    return timezone.make_aware(datetime.datetime.combine(day, DEFAULT_HOUR))


def _create(row, batch, user, now):
    v = row.values
    status = v["status"]
    if status == STATUS.CLOSED:
        changed = _local_datetime(v["closed_date"])
    elif status == STATUS.RESPONSE_SENT:
        changed = _local_datetime(v["reply_date"])
    else:
        changed = now  # an open complaint starts its reminder clock at the import
    complaint = Complaint(
        lodged_by=user, lodger_department="", factory=v["factory"], buyer=v["buyer"],
        contract_number=v["contract_number"], product_name=v["product_name"], delivery_date=v["delivery_date"],
        packing_type=v["packing_type"], pack_size=v["pack_size"], affected_quantity=v["affected_quantity"],
        quantity_unit=v["quantity_unit"], transport_type=v["transport_type"], vessel_name=v["vessel_name"],
        vehicle_number=v["vehicle_number"], container_number=v["container_number"], loading_port=v["loading_port"],
        discharge_port=v["discharge_port"], description=v["description"], complaint_type=v["complaint_type"],
        complaint_sub_type=v["complaint_sub_type"], type_details=v["type_details"], sample_sent=v["sample_sent"],
        do_reference=v["do_reference"], contact_person=v["contact_person"], contact_email=v["contact_email"],
        contact_phone=v["contact_phone"], contact_fax=v["contact_fax"], complaint_method=v["complaint_method"],
        status=status, created_at=v["issue_at"], submitted_at=v["issue_at"], status_changed_at=changed,
        hod_decision=v["hod_decision"], hod_reason=v["hod_reason"], is_imported=True,
        legacy_reference=v["legacy_reference"], imported_lodger_name=v["lodger"], import_batch=batch,
        coordinator_remarks=v["ims_remarks"], acknowledged_at=v["ims_at"],
        received_by=user if (v["ims_at"] or v["ims_remarks"]) else None,
    )  # fmt: skip
    if v["deleted"]:
        complaint.deleted_at, complaint.deleted_by, complaint.deleted_reason = now, user, v["deleted_reason"]
    complaint.assign_reference(today=v["issue_at"].astimezone(timezone.get_current_timezone()).date())
    complaint.save()

    if v["root_cause"] or v["correction"] or v["corrective_action"]:
        Investigation.objects.create(
            complaint=complaint, root_cause=v["root_cause"], correction=v["correction"], corrective_action=v["corrective_action"]
        )
    if v["lab_date"]:
        LabSection.objects.create(complaint=complaint, acknowledged_on=v["lab_date"])
    if v["reply_date"]:
        CustomerReply.objects.create(
            complaint=complaint, to_email=v["contact_email"], subject="Reply sent before the platform was used",
            body="The reply to the customer was sent before the platform was used. No copy of the email was kept here.",
            sent_by=user, sent_at=_local_datetime(v["reply_date"]),
        )  # fmt: skip
    if v["first_verified"] or v["second_verified"] or v["closed_date"] or v["remarks"]:
        Verification.objects.create(
            complaint=complaint, first_verified_date=v["first_verified"], second_verified_date=v["second_verified"],
            closed_at=_local_datetime(v["closed_date"]) if v["closed_date"] else None,
            closed_by=user if v["closed_date"] else None, closing_comment=v["remarks"],
        )  # fmt: skip
    ComplaintEvent.objects.create(
        complaint=complaint, action=ComplaintEvent.Action.IMPORTED, actor=user, to_status=status,
        comment=f"Imported from {batch.original_name}, row {row.row}. Stage set from the dates: {row.stage_label}.",
        created_at=now,
    )  # fmt: skip
    return complaint


def run_import(batch, user):
    """Create every complaint of a checked batch, or none. Returns the complaints, oldest first."""
    with transaction.atomic():
        batch = ImportBatch.objects.select_for_update().get(pk=batch.pk)
        if batch.status != ImportBatch.Status.PREVIEW:
            raise ImportProblem("This file has already been imported or was discarded.")
        with batch.file.open("rb") as handle:
            analysis = analyse(BytesIO(handle.read()))
        if not analysis.can_import:
            raise ImportProblem("The file still has errors. Fix them and upload it again.")
        now = timezone.now()
        created = []
        # Oldest first, so the running numbers of each factory follow the dates.
        for row in sorted(analysis.rows, key=lambda r: (r.values["issue_at"], r.row)):
            created.append(_create(row, batch, user, now))
        batch.status = ImportBatch.Status.IMPORTED
        batch.rows_total = len(analysis.rows)
        batch.imported_count = len(created)
        batch.imported_at = now
        batch.save()
    return created
