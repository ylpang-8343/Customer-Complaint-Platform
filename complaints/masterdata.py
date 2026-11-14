"""The lists the complaint form chooses from: customers, products (with their codes or SKUs) and pack sizes.

The Coordinator uploads a list from a spreadsheet (.xlsx) or a CSV file, so a name is never typed twice and never
mistyped. A row whose code (or, for pack sizes, name) is already in the list updates it; a new one is added. Nothing is
deleted by an upload: set Active to No to stop a name being offered. The upload is checked first and saved all together.
"""

import csv
from dataclasses import dataclass
from io import BytesIO, StringIO

from django.db import transaction
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from .models import Customer, PackSize, Product

MAX_ROWS = 20000
MAX_FILE_MB = 5


@dataclass(frozen=True)
class Dataset:
    slug: str
    title: str
    model: type
    key: str  # the column that says which entry a row is
    columns: tuple  # (header, field, required, example)
    help: str


DATASETS = {
    "customers": Dataset(
        "customers", "Customers", Customer, "code",
        (("Customer code", "code", True, "C0001"), ("Customer name", "name", True, "Example Buyer Ltd"), ("Active", "is_active", False, "Yes")),
        "The buyers and parties complaints can be raised about. The code must be unique.",
    ),  # fmt: skip
    "products": Dataset(
        "products", "Products", Product, "code",
        (("Product code / SKU", "code", True, "P0001"), ("Product name", "name", True, "RBD Palm Olein"), ("Active", "is_active", False, "Yes")),
        "The products complaints can be raised about, with their code or SKU. The code must be unique.",
    ),  # fmt: skip
    "pack-sizes": Dataset(
        "pack-sizes", "Pack sizes", PackSize, "name",
        (("Pack size", "name", True, "Drum 200 L"), ("Active", "is_active", False, "Yes")),
        "The pack sizes offered when the packing type is Pack, for example Drum 200 L or Carton 12 x 1 L.",
    ),  # fmt: skip
}


class UploadError(Exception):
    """The file cannot be used at all (not a spreadsheet, too big, headers missing)."""


def _norm(text):
    return " ".join(str(text or "").split()).lower()


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # a code typed as a number in Excel: 1001, not 1001.0
    return str(value).strip()


def _read(handle, name):
    """The rows of the file as lists of text, header row first."""
    content = handle.read()
    if name.lower().endswith(".csv"):
        text = content.decode("utf-8-sig", errors="replace")
        return [[_cell(c) for c in row] for row in csv.reader(StringIO(text))]
    try:
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
        return [[_cell(c) for c in row] for row in workbook.worksheets[0].iter_rows(values_only=True)]
    except Exception:
        raise UploadError("The file could not be read. Use the template (.xlsx) or a CSV file.")


def check(dataset, handle, name):
    """Read and check an upload. Returns (rows, errors): rows are dicts of field values, errors are (row, message)."""
    table = _read(handle, name)
    while table and not any(table[-1]):
        table.pop()
    if not table:
        raise UploadError("The file is empty.")
    headers = {_norm(h): i for i, h in enumerate(table[0]) if h}
    missing = [h for h, _, required, _ in dataset.columns if required and _norm(h) not in headers]
    if missing:
        raise UploadError("The first row must have the headers of the template. Missing: " + ", ".join(missing) + ".")
    if len(table) - 1 > MAX_ROWS:
        raise UploadError(f"The file has more than {MAX_ROWS} rows. Upload it in parts.")

    rows, errors, seen = [], [], {}
    max_length = {f.name: f.max_length for f in dataset.model._meta.get_fields() if getattr(f, "max_length", None)}
    for number, raw in enumerate(table[1:], start=2):
        if not any(raw):
            continue
        values, problems = {}, []
        for header, field, required, _ in dataset.columns:
            index = headers.get(_norm(header))
            value = raw[index].strip() if index is not None and index < len(raw) else ""
            if field == "is_active":
                low = value.lower()
                if low in ("", "yes", "y", "true", "1"):
                    values[field] = True
                elif low in ("no", "n", "false", "0"):
                    values[field] = False
                else:
                    problems.append(f"{header}: '{value}' is not Yes or No.")
                continue
            if required and not value:
                problems.append(f"{header} is needed.")
            elif len(value) > max_length.get(field, 10**9):
                problems.append(f"{header} is too long (at most {max_length[field]} characters).")
            values[field] = value
        key = _norm(values.get(dataset.key))
        if key and key in seen:
            problems.append(f"'{values[dataset.key]}' appears twice (also in row {seen[key]}).")
        elif key:
            seen[key] = number
        errors += [(number, problem) for problem in problems]
        rows.append(values)
    if not rows:
        raise UploadError("The file has headers but no rows.")
    return rows, errors


def apply(dataset, rows):
    """Save the rows: add the new entries and update the ones already in the list. Returns (added, updated, unchanged)."""
    existing = {_norm(getattr(o, dataset.key)): o for o in dataset.model.objects.all()}
    added = updated = unchanged = 0
    with transaction.atomic():
        for values in rows:
            entry = existing.get(_norm(values[dataset.key]))
            if entry is None:
                dataset.model.objects.create(**values)
                added += 1
                continue
            changed = [f for f, v in values.items() if getattr(entry, f) != v]
            if changed:
                for field in changed:
                    setattr(entry, field, values[field])
                entry.save()
                updated += 1
            else:
                unchanged += 1
    return added, updated, unchanged


def build_template(dataset):
    """An .xlsx with the headers and one example row."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = dataset.title
    fill = PatternFill("solid", start_color="7A4A10")
    for number, (header, _, required, example) in enumerate(dataset.columns, start=1):
        cell = sheet.cell(row=1, column=number, value=header)
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), fill
        cell.alignment = Alignment(vertical="center")
        sheet.cell(row=2, column=number, value=example).font = Font(italic=True, color="7D705E")
        sheet.column_dimensions[cell.column_letter].width = 34 if number == 2 else 22
    note = workbook.create_sheet("Instructions")
    note.column_dimensions["A"].width = 100
    lines = [
        dataset.title,
        dataset.help,
        "Replace the example row (row 2) with your own and add one row for each entry. Keep the first row as it is.",
        "A row whose " + dataset.columns[0][0] + " is already in the platform updates that entry; a new one is added. Nothing is deleted.",
        "Active: Yes (or empty) offers the name on the complaint form; No stops it being offered. Old complaints keep their name.",
    ]
    for row, text in enumerate(lines, start=1):
        cell = note.cell(row=row, column=1, value=text)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        cell.font = Font(bold=row == 1, size=14 if row == 1 else 11)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
