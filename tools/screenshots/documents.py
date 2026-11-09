"""Step 6 in a real browser: the PDF report and audit pack of one complaint, and the import of past complaints.

Needs, besides playwright and openpyxl: pip install pypdfium2 pillow   (to turn the PDF pages into pictures)
"""
import datetime
import io
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

import pypdfium2 as pdfium
from openpyxl import load_workbook
from playwright.sync_api import sync_playwright

PROJECT = Path(__file__).resolve().parents[2]
PY = str(next(p for p in (PROJECT / ".venv" / "Scripts" / "python.exe", PROJECT / ".venv" / "bin" / "python") if p.exists()))
SCRATCH = Path(tempfile.gettempdir()) / "complaint-screenshots"
SCRATCH.mkdir(exist_ok=True)
OUT = PROJECT / "docs" / "img"
BASE = "http://127.0.0.1:8769"
PASSWORD = "demo-pass-123"
LOG = SCRATCH / "server6.log"


def reset_demo_database():
    """Start from an empty database with the reference data and the demo users, and no uploaded files."""
    (PROJECT / "db.sqlite3").unlink(missing_ok=True)
    shutil.rmtree(PROJECT / "media", ignore_errors=True)
    subprocess.run([PY, "manage.py", "migrate", "-v", "0"], cwd=PROJECT, check=True)
    subprocess.run([PY, "manage.py", "seed", "--demo"], cwd=PROJECT, check=True, stdout=subprocess.DEVNULL)


reset_demo_database()
server = subprocess.Popen(
    [PY, "manage.py", "runserver", "8769", "--noreload"], cwd=PROJECT, stdout=open(LOG, "w"), stderr=subprocess.STDOUT,
)
failures = []


def check(label, condition):
    print(("ok   " if condition else "FAIL ") + label)
    if not condition:
        failures.append(label)


today = datetime.date.today()


def ago(days):
    return today - datetime.timedelta(days=days)


# Eight past complaints in every state. The template's own headers are used to fill them in.
GOOD_ROWS = [
    dict(factory="FA", legacy_reference="FA-021", lodger="A. Tan", issue_date=ago(140), buyer="Alpha Foods Ltd", product_name="Refined oil",
         affected_quantity=20, quantity_unit="Metric Ton", complaint_type="Packaging", complaint_sub_type="Packaging - Leaking",
         contact_email="alpha@example.com", correction="Replaced the flexibags.", corrective_action="Audit the supplier.",
         reply_date=ago(110), hod_decision="Valid", first_verified=ago(95), closed_date=ago(90), remarks="Effective."),
    dict(factory="FA", legacy_reference="FA-022", lodger="B. Lim", issue_date=ago(120), buyer="Bravo Trading Sdn Bhd", product_name="Crude oil",
         complaint_type="Product Quality", complaint_sub_type="Quality - Off Spec", ims_date=ago(119), reply_date=ago(100),
         hod_decision="Invalid", hod_reason="The product met the contract specification."),
    dict(factory="FB", legacy_reference="FB-007", lodger="C. Wong", issue_date=ago(100), buyer="Charlie Imports",
         complaint_type="Logistic", complaint_sub_type="Logistic - Shortage"),
    dict(factory="FB", legacy_reference="FB-008", lodger="C. Wong", issue_date=ago(80), buyer="Delta Foods", complaint_type="Documentation",
         complaint_sub_type="Documentation - Missing", ims_date=ago(79), root_cause="Certificate was not attached to the shipping set."),
    dict(factory="FC", legacy_reference="FC-003", lodger="D. Ong", issue_date=ago(60), buyer="Echo Trading", complaint_type="Packaging",
         complaint_sub_type="Packaging - Dented", reply_date=ago(40), hod_decision="Valid", closed_date=ago(20), remarks="Closed after two checks."),
    dict(factory="FD", legacy_reference="FD-010", lodger="E. Lee", issue_date=ago(40), buyer="Foxtrot Ltd", complaint_type="Logistic",
         complaint_sub_type="Logistic - Transport Damage", deleted="Yes", deleted_reason="Entered twice in the old log."),
    dict(factory="FE", legacy_reference="FE-002", lodger="F. Goh", issue_date=ago(30), buyer="Golf Distribution", complaint_type="Product Quality",
         complaint_sub_type="Quality - Foreign Matter", sample_sent="Yes", lab_date=ago(28)),
    dict(factory="FA", legacy_reference="FA-023", lodger="A. Tan", issue_date=ago(15), buyer="Hotel Foods", complaint_type="Documentation",
         complaint_sub_type="Documentation - Late/Delay"),
]


def fill(template_path, rows, out_path):
    book = load_workbook(template_path)
    sheet = book["Complaints"]
    headers = {cell.value: cell.column for cell in sheet[1]}
    key_to_header = KEYS
    for number, row in enumerate(rows, start=2):
        for key, value in row.items():
            sheet.cell(row=number, column=headers[key_to_header[key]], value=value)
    book.save(out_path)


# The header of each key, read from the template itself (same order as the importer's columns)
KEYS = {}

try:
    for _ in range(40):
        try:
            urllib.request.urlopen(BASE + "/login/", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    else:
        sys.exit("server did not start")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")

        def sign_in(username, width=1440, height=900):
            ctx = browser.new_context(viewport={"width": width, "height": height}, accept_downloads=True)
            page = ctx.new_page()
            page.goto(BASE + "/login/")
            page.fill("input[name=username]", username)
            page.fill("input[name=password]", PASSWORD)
            page.click("button[type=submit]")
            page.wait_for_load_state()
            return ctx, page

        def shot(page, name, full=True):
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
            print("saved", name)

        ctx, page = sign_in("mr")

        # --- Import: the page, the template, a file with mistakes, the corrected file, the result
        page.goto(BASE + "/import/")
        check("the Coordinator has an Import page", "Download the template" in page.content())
        shot(page, "57-import-page")
        with page.expect_download() as download:
            page.click("a:has-text('Download the template')")
        template = SCRATCH / "complaint-import-template.xlsx"
        download.value.save_as(str(template))
        book = load_workbook(template)
        check("the template has the instructions, the sheet and the lists", book.sheetnames == ["Instructions", "Complaints", "Lists"])
        headers = [cell.value for cell in book["Complaints"][1]]
        # the order of the importer's columns, by key, so the script does not need the project's code
        order = ["factory", "legacy_reference", "lodger", "issue_date", "buyer", "contract_number", "product_name", "delivery_date",
                 "packing_type", "pack_size", "affected_quantity", "quantity_unit", "transport_type", "vessel_name", "vehicle_number",
                 "container_number", "loading_port", "discharge_port", "description", "complaint_type", "complaint_sub_type",
                 "type_details", "sample_sent", "do_reference", "contact_person", "contact_email", "contact_phone", "contact_fax",
                 "complaint_method", "lab_date", "ims_date", "ims_remarks", "root_cause", "correction", "corrective_action",
                 "reply_date", "hod_decision", "hod_reason", "first_verified", "second_verified", "closed_date", "remarks",
                 "deleted", "deleted_reason"]
        check("the template has the 44 columns", len(headers) == len(order) == 44)
        KEYS.update(dict(zip(order, headers)))

        bad_rows = [dict(r) for r in GOOD_ROWS]
        bad_rows[2]["factory"] = "FZ"
        bad_rows[4]["complaint_type"] = "Packging"
        bad_rows[4]["complaint_sub_type"] = "Packaging - Dented"
        bad_rows[1]["hod_decision"] = None
        bad_file = SCRATCH / "past-complaints-with-mistakes.xlsx"
        fill(template, bad_rows, bad_file)
        page.set_input_files("input[name=file]", str(bad_file))
        page.click("button:has-text('Upload and check')")
        page.wait_for_load_state()
        check("mistakes are listed with their row and column", "is not a factory" in page.content() and "is not a complaint type" in page.content() and "Say whether the complaint is Valid or Invalid" in page.content())
        check("no import button while there are errors", page.locator("button[name=confirm]").count() == 0)
        shot(page, "58-import-errors")

        good_file = SCRATCH / "past-complaints.xlsx"
        fill(template, GOOD_ROWS, good_file)
        page.goto(BASE + "/import/")
        page.set_input_files("input[name=file]", str(good_file))
        page.click("button:has-text('Upload and check')")
        page.wait_for_load_state()
        check("the corrected file is ready to import", page.locator("button[name=confirm]").count() == 1 and "Import 8 complaints" in page.content())
        check("each row shows the stage it gets", all(stage in page.content() for stage in ("Closed", "Customer Response Sent", "PIC investigation", "Submitted")))
        shot(page, "59-import-preview")
        page.click("button[name=confirm]")
        page.wait_for_load_state()
        check("eight complaints imported", "Imported 8 complaints" in page.content())
        check("references follow the dates", "FA/2026/0001" in page.content() and "FB/2026/0001" in page.content())
        shot(page, "60-import-done")

        page.click("a:has-text('FA/2026/0001')")
        page.wait_for_load_state()
        check("an imported complaint says where it came from", "Imported from the previous log" in page.content() and "FA-021" in page.content())
        check("it keeps the name from the old log", "A. Tan" in page.content())
        shot(page, "61-detail-imported")

        page.goto(BASE + "/dashboard/")
        check("imported complaints are in the dashboard", re.search(r"<b>8</b><span>Total number of complaints", page.content()) is not None)

        # --- The PDF report and the audit pack of a complaint that went through every step
        made = subprocess.run([PY, "manage.py", "shell"], cwd=PROJECT, input=(Path(__file__).parent / "report_data.py").read_text(encoding="utf-8"), capture_output=True, text=True)
        match = re.search(r"created (\S+) (\d+)", made.stdout + made.stderr)
        check("the full-cycle complaint was created", match is not None)
        reference, pk = match.group(1), match.group(2)
        page.goto(BASE + f"/complaints/{pk}/")
        check("the page offers the PDF report and the audit pack", "PDF report" in page.content() and "Audit pack" in page.content())
        shot(page, "62-detail-report-buttons", full=False)

        pdf_response = ctx.request.get(BASE + f"/complaints/{pk}/report.pdf")
        check("the report is a PDF", pdf_response.status == 200 and pdf_response.headers["content-type"] == "application/pdf")
        document = pdfium.PdfDocument(pdf_response.body())
        pages = len(document)
        check("the report has several pages", pages >= 3)
        for index, name in enumerate(("63-pdf-report-page-1", "64-pdf-report-page-2", "65-pdf-report-page-3")):
            if index < pages:
                document[index].render(scale=1.35).to_pil().save(OUT / f"{name}.png")
                print("saved", name)
        text = "".join(document[i].get_textpage().get_text_range() for i in range(pages))
        check("the PDF has every section", all(h in text.upper() for h in ("CUSTOMER COMPLAINT REPORT", "LABORATORY SECTION", "SCAR TO AN EXTERNAL PARTY", "REVIEW AND APPROVAL RECORDS", "REPLY TO THE CUSTOMER", "EFFECTIVENESS AND CLOSING", "HISTORY")))
        check("the footer numbers the pages", f"Page 1 of {pages}" in text and f"Page {pages} of {pages}" in text)

        pack = ctx.request.get(BASE + f"/complaints/{pk}/audit-pack.zip")
        archive = zipfile.ZipFile(io.BytesIO(pack.body()))
        names = archive.namelist()
        print("audit pack:", *names, sep="\n   ")
        check("the audit pack holds the report, the SCAR, the reply and the documents",
              any(n.endswith("-report.pdf") for n in names) and any("/scar/SCAR-" in n for n in names)
              and any(n.endswith("reply-to-customer.txt") for n in names) and any(n.endswith("lab-report.pdf") for n in names)
              and any(n.endswith("leak-photo.png") for n in names))
        ctx.close()

        # A factory user can get the report of their own factory's complaint only
        ctx, page = sign_in("fb.pic")
        refused = ctx.request.get(BASE + f"/complaints/{pk}/report.pdf")
        check("another factory cannot download the report", refused.status == 404)
        check("a non-Coordinator has no Import", "Import</a>" not in page.content())
        ctx.close()

        # Phone width
        ctx, page = sign_in("mr", width=390, height=800)
        page.goto(BASE + "/import/")
        check("no sideways scrolling on the Import page at phone width", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
        shot(page, "66-mobile-import", full=False)
        ctx.close()
        browser.close()
finally:
    server.terminate()
    time.sleep(0.5)

print("\nFAILURES:", failures or "none")
