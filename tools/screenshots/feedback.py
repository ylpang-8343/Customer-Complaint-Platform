"""The feedback of the demo in a real browser: automatic assignment, several factories, the master data upload,
the new columns and filters, the dashboard and the reply as a file."""
import datetime
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

PROJECT = Path(__file__).resolve().parents[2]
PY = str(next(p for p in (PROJECT / ".venv" / "Scripts" / "python.exe", PROJECT / ".venv" / "bin" / "python") if p.exists()))
SCRATCH = Path(tempfile.gettempdir()) / "complaint-screenshots"
SCRATCH.mkdir(exist_ok=True)
OUT = PROJECT / "docs" / "img"
BASE = "http://127.0.0.1:8772"
PASSWORD = "demo-pass-123"
LOG = SCRATCH / "server-feedback.log"


def ago(days):
    """The form accepts no date in the future."""
    return (datetime.date.today() - datetime.timedelta(days=days)).isoformat()


def reset_demo_database():
    (PROJECT / "db.sqlite3").unlink(missing_ok=True)
    shutil.rmtree(PROJECT / "media", ignore_errors=True)
    subprocess.run([PY, "manage.py", "migrate", "-v", "0"], cwd=PROJECT, check=True)
    subprocess.run([PY, "manage.py", "seed", "--demo"], cwd=PROJECT, check=True, stdout=subprocess.DEVNULL)


reset_demo_database()
made = subprocess.run(
    [PY, "manage.py", "shell"], cwd=PROJECT, input=(Path(__file__).parent / "demo_data.py").read_text(encoding="utf-8"),
    capture_output=True, text=True,
)
print((made.stdout + made.stderr).strip().splitlines()[-1])

server = subprocess.Popen(
    [PY, "manage.py", "runserver", "8772", "--noreload"], cwd=PROJECT, stdout=open(LOG, "w"), stderr=subprocess.STDOUT,
)
failures = []


def check(label, condition):
    print(("ok   " if condition else "FAIL ") + label)
    if not condition:
        failures.append(label)


(SCRATCH / "customers.csv").write_text(
    "Customer code,Customer name,Active\nC0101,Mike Foods Sdn Bhd,Yes\nC0102,November Trading,Yes\nC0001,Example Buyer Ltd,Yes\n", encoding="utf-8"
)

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

        def choose(page, name, text):
            value = page.eval_on_selector_all(
                f"select[name={name}] option", "(els, t) => (els.find(e => e.textContent.includes(t)) || {}).value", text
            )
            page.select_option(f"select[name={name}]", value=value)

        def fill_common(page, complaint_type, sub_type):
            page.goto(BASE + "/complaints/new/")
            page.select_option("select[name=factory]", label="Factory A")
            choose(page, "customer", "Alpha Foods")
            page.fill("input[name=contract_number]", "SC-000456")
            choose(page, "product", "RBD Palm Olein")
            page.fill("input[name=received_date]", ago(3))
            page.select_option("select[name=packing_type]", label="Bulk")
            page.fill("input[name=affected_quantity]", "12")
            page.select_option("select[name=quantity_unit]", label="Metric Ton")
            page.select_option("select[name=complaint_type]", label=complaint_type)
            if sub_type:
                page.select_option("select[name=complaint_sub_type]", label=sub_type)
            page.select_option("select[name=sample_sent]", label="No")
            page.fill("textarea[name=issue_identified]", "Short delivery reported on arrival.")
            page.fill("textarea[name=customer_findings]", "The weighbridge showed 12 t less than the delivery order.")
            page.fill("textarea[name=customer_request]", "A credit note for the missing quantity.")
            page.fill("input[name=contact_email]", "contact@example.com")

        # 1. A shipment complaint goes to Shipping by itself, a local one to Operation
        ctx, page = sign_in("sales")
        fill_common(page, "Logistic", "Logistic - Shortage")
        page.select_option("select[name=transport_type]", label="Shipment - Bulk")
        page.fill("input[name=vessel_name]", "MV Example")
        page.fill("input[name=loading_port]", "Example Loading Port")
        page.fill("input[name=discharge_port]", "Example Discharge Port")
        page.fill("input[name=loading_date]", ago(25))
        page.fill("input[name=discharge_date]", ago(8))
        check("a shipment names the loading and discharge dates", page.locator('[data-field=loading_date]').is_visible() and page.locator('[data-field=discharge_date]').is_visible() and not page.locator('[data-field=delivery_date]').is_visible())
        check("a shipment's places are called from and to", "From (loading port / city)" in page.locator('[data-field=loading_port]').inner_text())
        check("no date can be picked after today", page.get_attribute("input[name=received_date]", "max") == ago(0))
        shot(page, "71-form-shipment")
        page.click("button[name=submit]")
        page.wait_for_load_state()
        check("a shipment complaint is assigned to Shipping", "assigned to the Shipping department" in page.content())
        shot(page, "72-auto-assigned")

        fill_common(page, "Logistic", "Logistic - Shortage")
        page.select_option("select[name=transport_type]", label="Local Delivery - Bulk")
        page.fill("input[name=vehicle_number]", "ABC 1234")
        page.fill("input[name=loading_port]", "Factory gate")
        page.fill("input[name=discharge_port]", "Buyer's tank farm")
        page.fill("input[name=delivery_date]", ago(4))
        check("a local delivery shows the delivery date and its own place labels", page.locator('[data-field=delivery_date]').is_visible() and not page.locator('[data-field=loading_date]').is_visible() and "Place of loading" in page.locator('[data-field=loading_port]').inner_text() and "Destination" in page.locator('[data-field=discharge_port]').inner_text())
        shot(page, "73-form-local")
        page.click("button[name=submit]")
        page.wait_for_load_state()
        check("a local complaint is assigned to Operation", "assigned to the Operation department" in page.content())

        # a future date is stopped by the server as well
        fill_common(page, "Packaging", "Packaging - Leaking")
        page.evaluate("document.querySelector('input[name=received_date]').removeAttribute('max')")
        page.fill("input[name=received_date]", (datetime.date.today() + datetime.timedelta(days=2)).isoformat())
        page.click("button[name=save]")
        page.wait_for_load_state()
        check("a future received date is refused", "cannot be in the future" in page.content())
        ctx.close()

        # 2. The Coordinator: master data
        ctx, page = sign_in("mr")
        page.goto(BASE + "/masterdata/")
        check("the master data page offers the three lists", all(t in page.content() for t in ("Customers", "Products", "Pack sizes")))
        shot(page, "74-masterdata")
        page.locator("input[name=file]").first.set_input_files(str(SCRATCH / "customers.csv"))
        page.locator("button:has-text('Upload')").first.click()
        page.wait_for_load_state()
        check("the upload reports what it added and updated", "2 added" in page.content() and "unchanged" in page.content())
        shot(page, "75-masterdata-uploaded")

        page.goto(BASE + "/")
        heads = [h.strip().title() for h in page.locator("table.list thead th").all_inner_texts()]
        check("My complaints has the columns of the feedback", heads == ["Reference", "Factory", "Buyer", "Product", "Type", "Status", "Waiting For", "Days Open"])
        check("My complaints shows 10 rows to start with", page.locator("table.list tbody tr").count() == 10)
        check("My complaints names the person it waits for", "PIC - " in page.content())
        check("the pager gives the total", re.search(r"Page 1 of \d+ \(\d+ complaints\)", page.content()) is not None)
        shot(page, "76-home-columns")
        page.select_option("select[name=page_size]", label="25 per page")
        page.click("button:has-text('Apply')")
        page.wait_for_load_state()
        check("the page size can be changed", page.locator("table.list tbody tr").count() > 10)

        page.goto(BASE + "/dashboard/")
        check("the dashboard counts by factory", "Complaints by factory" in page.content())
        check("the reasons link to the complaints", page.locator("section:has-text('Invalid and deleted') table a").count() >= 1)
        shot(page, "77-dashboard-feedback")

        page.goto(BASE + "/log/?status=deleted")
        check("the log can be filtered by status", page.locator("table.log tbody tr").count() == 1)
        with page.expect_download() as download:
            page.click("a:has-text('Export to Excel')")
        check("the export keeps the filter", download.value.path() is not None and "status=deleted" in page.url + page.locator("a:has-text('Export to Excel')").get_attribute("href"))

        # the reply as a file
        page.goto(BASE + "/?status=response_sent")
        link = page.locator("table.list tbody a").first
        link.click()
        page.wait_for_load_state()
        check("the reply is a file to download, not the whole email", page.locator("a:has-text('Reply to customer')").count() >= 1 and "Dear" not in page.locator("section", has=page.locator("h2:has-text('Reply sent to the customer')")).first.inner_text())
        with page.expect_download() as download:
            page.click("a:has-text('Reply to customer')")
        check("the reply downloads as a PDF", download.value.suggested_filename.lower().endswith(".pdf"))
        page.locator("section", has=page.locator("h2:has-text('Reply sent to the customer')")).first.scroll_into_view_if_needed()
        shot(page, "78-reply-as-file", full=False)
        ctx.close()

        # 3. A trader sees only the factories assigned
        ctx, page = sign_in("alan.trader")
        page.goto(BASE + "/?page_size=100")
        codes = set(re.findall(r"\b(F[A-E])/\d{4}/\d+", page.content()))
        check("the trader sees Factories A and B only", codes == {"FA", "FB"})
        shot(page, "79-trader-home")
        ctx.close()
        browser.close()
finally:
    server.terminate()
    time.sleep(0.5)

print("\nFAILURES:", failures or "none")
sys.exit(1 if failures else 0)
