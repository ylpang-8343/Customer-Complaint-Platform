"""Drive the lodging form in a real browser, assert the conditional fields, save screenshots."""
import datetime
import os
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
BASE = "http://127.0.0.1:8765"
PASSWORD = "demo-pass-123"
LOG = SCRATCH / "server.log"

(SCRATCH / "leak-photo.png").write_bytes(
    bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")
)
(SCRATCH / "supplier-report.pdf").write_bytes(b"%PDF-1.4\n%fake report for the demo\n")



def ago(days):
    """A date in the past: the form does not accept a date in the future."""
    return (datetime.date.today() - datetime.timedelta(days=days)).isoformat()


def choose(page, name, text):
    """Pick the entry of a list (customer, product, pack size) whose label contains the text."""
    value = page.eval_on_selector_all(
        f"select[name={name}] option", "(els, t) => (els.find(e => e.textContent.includes(t)) || {}).value", text
    )
    page.select_option(f"select[name={name}]", value=value)


def reset_demo_database():
    """Start from an empty database with the reference data and the demo users, and no uploaded files."""
    (PROJECT / "db.sqlite3").unlink(missing_ok=True)
    shutil.rmtree(PROJECT / "media", ignore_errors=True)
    subprocess.run([PY, "manage.py", "migrate", "-v", "0"], cwd=PROJECT, check=True)
    subprocess.run([PY, "manage.py", "seed", "--demo"], cwd=PROJECT, check=True, stdout=subprocess.DEVNULL)


reset_demo_database()

server = subprocess.Popen(
    [PY, "manage.py", "runserver", "8765", "--noreload"],
    cwd=PROJECT, env={**os.environ, "AUTO_ASSIGN": "0"}, stdout=open(LOG, "w"), stderr=subprocess.STDOUT,
)
failures = []


def check(label, condition):
    print(("ok   " if condition else "FAIL ") + label)
    if not condition:
        failures.append(label)


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

        def sign_in(username, width=1440, height=860):
            ctx = browser.new_context(viewport={"width": width, "height": height})
            page = ctx.new_page()
            page.goto(BASE + "/login/")
            page.fill("input[name=username]", username)
            page.fill("input[name=password]", PASSWORD)
            page.click("button[type=submit]")
            page.wait_for_load_state()
            return ctx, page

        def shot(page, name, full=False):
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
            print("saved", name)

        # Sign-in page and a failed sign-in (no sidebar when signed out)
        anon = browser.new_context(viewport={"width": 1440, "height": 700})
        apage = anon.new_page()
        apage.goto(BASE + "/login/")
        check("sign-in page has no sidebar", apage.locator(".sidebar").count() == 0)
        shot(apage, "01-login")
        apage.fill("input[name=username]", "fa.pic")
        apage.fill("input[name=password]", "wrong-password")
        apage.click("button[type=submit]")
        apage.wait_for_load_state()
        check("failed sign-in shows an error", "Please enter a correct username and password" in apage.content())
        shot(apage, "02-login-error")
        anon.close()

        ctx, page = sign_in("sales")
        check("sidebar shows who is signed in", "Signed in as" in page.inner_text(".whoami"))
        visible = lambda name: page.locator(f'[data-field="{name}"]').is_visible()
        page.goto(BASE + "/complaints/new/")

        # Nothing conditional is open on a blank form
        for name in ("pack_size_option", "vessel_name", "vehicle_number", "container_number", "complaint_sub_type", "type_details"):
            check(f"blank form hides {name}", not visible(name))
        shot(page, "15-form-blank", full=True)

        # e. Packing type
        page.select_option("select[name=packing_type]", label="Pack")
        check("Pack opens pack size", visible("pack_size_option"))
        page.select_option("select[name=packing_type]", label="Bulk")
        check("Bulk closes pack size", not visible("pack_size_option"))
        page.select_option("select[name=packing_type]", label="Pack")

        # g. Transport type
        page.select_option("select[name=transport_type]", label="Shipment - Bulk")
        check("Shipment - Bulk: vessel only", visible("vessel_name") and not visible("vehicle_number") and not visible("container_number"))
        page.select_option("select[name=transport_type]", label="Shipment - Container")
        check("Shipment - Container: vessel + container", visible("vessel_name") and visible("container_number") and not visible("vehicle_number"))
        page.select_option("select[name=transport_type]", label="Local Delivery - Bulk")
        check("Local Delivery - Bulk: vehicle only", visible("vehicle_number") and not visible("vessel_name") and not visible("container_number"))
        page.select_option("select[name=transport_type]", label="Local Delivery - Container")
        check("Local Delivery - Container: vehicle + container", visible("vehicle_number") and visible("container_number") and not visible("vessel_name"))
        page.select_option("select[name=transport_type]", label="Shipment - Container")

        # j. Complaint type and sub-type
        def enabled_sub_types():
            return page.eval_on_selector_all(
                "select[name=complaint_sub_type] option",
                "els => els.filter(e => e.value && !e.disabled).map(e => e.textContent.trim())",
            )

        page.select_option("select[name=complaint_type]", label="Packaging")
        check("Packaging shows its 5 sub-types only", visible("complaint_sub_type") and len(enabled_sub_types()) == 5 and all(s.startswith("Packaging") for s in enabled_sub_types()))
        check("Packaging does not ask for short details", not visible("type_details"))
        page.select_option("select[name=complaint_sub_type]", label="Packaging - Others")
        check("an Others sub-type opens short details", visible("type_details"))
        page.select_option("select[name=complaint_type]", label="Logistic")
        check("changing the type clears a sub-type of the old type", page.eval_on_selector("select[name=complaint_sub_type]", "e => e.value") == "" and not visible("type_details"))
        page.select_option("select[name=complaint_type]", label="Others")
        check("type Others: no sub-type, short details open", not visible("complaint_sub_type") and visible("type_details"))
        page.select_option("select[name=complaint_type]", label="Packaging")
        page.select_option("select[name=complaint_sub_type]", label="Packaging - Leaking")

        # A draft needs only the factory
        page.reload()
        page.select_option("select[name=factory]", label="Factory A")
        choose(page, "customer", "Example Buyer Ltd")
        page.click("button[name=save]")
        page.wait_for_load_state()
        check("draft saved with a reference", "Draft FA/2026/0001 saved." in page.content())
        check("draft page keeps the entered buyer", page.eval_on_selector("select[name=customer]", "e => e.selectedOptions[0].textContent").endswith("Example Buyer Ltd"))
        check("factory is locked once the reference exists", page.locator("select[name=factory]").is_disabled())
        shot(page, "16-draft-saved", full=True)

        # Submitting an incomplete form shows what is missing and keeps the draft
        page.click("button[name=submit]")
        page.wait_for_load_state()
        check("incomplete submit stays on the form with errors", page.locator(".has-error").count() >= 8)
        check("incomplete submit explains the draft is saved", "The draft is saved" in page.content())
        shot(page, "17-form-errors", full=True)

        # Complete it, attach two files and submit
        page.fill("input[name=contract_number]", "SC-000123")
        choose(page, "product", "Sample Product")
        page.fill("input[name=received_date]", ago(5))
        page.select_option("select[name=packing_type]", label="Pack")
        choose(page, "pack_size_option", "Flexibag")
        page.fill("input[name=affected_quantity]", "20")
        page.select_option("select[name=quantity_unit]", label="Metric Ton")
        page.select_option("select[name=transport_type]", label="Shipment - Container")
        page.fill("input[name=vessel_name]", "MV Example")
        page.fill("input[name=container_number]", "ABCD1234567")
        page.fill("input[name=loading_port]", "Example Loading Port")
        page.fill("input[name=discharge_port]", "Example Discharge Port")
        page.fill("input[name=loading_date]", ago(20))
        page.fill("input[name=discharge_date]", ago(7))
        page.select_option("select[name=complaint_type]", label="Packaging")
        page.select_option("select[name=complaint_sub_type]", label="Packaging - Leaking")
        page.select_option("select[name=sample_sent]", label="No")
        page.fill("textarea[name=issue_identified]", "Flexibag leaked during discharge; customer reports product loss at the receiving terminal.")
        page.fill("input[name=contact_person]", "Sample Contact")
        page.fill("input[name=contact_email]", "contact@example.com")
        page.select_option("select[name=complaint_method]", label="Email")
        page.set_input_files("input[name=attachments]", [str(SCRATCH / "leak-photo.png"), str(SCRATCH / "supplier-report.pdf")])
        page.click("button[name=submit]")
        page.wait_for_load_state()
        check("complete form is submitted", "was submitted to the Coordinator" in page.content())
        check("detail page shows Submitted", page.locator(".status-submitted").count() == 1)
        check("both files are listed", "leak-photo.png" in page.content() and "supplier-report.pdf" in page.content())
        shot(page, "18-complaint-submitted", full=True)

        with page.expect_download() as download:
            page.click("text=leak-photo.png")
        check("file downloads under its original name", download.value.suggested_filename == "leak-photo.png")

        # A submitted complaint cannot be opened for editing
        page.goto(page.url.rstrip("/") + "/edit/")
        check("submitted complaint redirects away from the edit page", "/edit/" not in page.url)

        # Lists
        page.goto(BASE + "/")
        shot(page, "19-home-list")
        ctx.close()

        # Another user's draft is private; the Coordinator sees the submitted complaint
        ctx2, page2 = sign_in("sales")
        page2.goto(BASE + "/complaints/new/")
        page2.select_option("select[name=factory]", label="Factory B")
        page2.click("button[name=save]")
        page2.wait_for_load_state()
        ctx2.close()

        ctx3, page3 = sign_in("mr")
        page3.goto(BASE + "/")
        check("Coordinator sees the submitted complaint", "FA/2026/0001" in page3.content())
        check("Coordinator does not see someone else's draft", "FB/2026/0001" not in page3.content())
        shot(page3, "05-home-coordinator")
        ctx3.close()

        ctx4, page4 = sign_in("fb.pic")
        page4.goto(BASE + "/")
        check("a Factory B user does not see the Factory A complaint", "FA/2026/0001" not in page4.content())
        shot(page4, "04-home-other-factory")
        ctx4.close()

        ctx5, page5 = sign_in("fa.pic")
        page5.goto(BASE + "/")
        check("a Factory A PIC sees the Factory A complaint", "FA/2026/0001" in page5.content())
        shot(page5, "03-home-pic")
        page5.goto(BASE + "/help/")
        check("help highlights the PIC guide", page5.locator(".guide.mine h3").inner_text().startswith("PICs and laboratory"))
        page5.fill("#task-search", "attach")
        check("help search narrows the list", 0 < page5.locator("#tasks details:visible").count() < page5.locator("#tasks details").count() and page5.inner_text("#task-count") == str(page5.locator("#tasks details:visible").count()))
        page5.fill("#task-search", "")
        page5.locator("#tasks details").first.locator("summary").click()
        shot(page5, "21-help")
        ctx5.close()

        # Filters: the lodger's own draft, then a phone-width view
        ctx6, page6 = sign_in("sales")
        page6.goto(BASE + "/")
        page6.select_option("select[name=status]", label="Draft")
        page6.click("button:has-text('Apply')")
        page6.wait_for_load_state()
        check("status filter keeps only drafts", page6.locator("table.list tbody tr").count() == 1 and "FB/2026/0001" in page6.content())
        shot(page6, "22-home-filtered")
        ctx6.close()

        ctx7, page7 = sign_in("sales", width=390, height=800)
        page7.goto(BASE + "/")
        shot(page7, "23-mobile-list", full=True)
        page7.goto(BASE + "/complaints/new/")
        check("no sideways scrolling on a phone", page7.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
        shot(page7, "24-mobile-form")
        ctx7.close()

        browser.close()
finally:
    server.terminate()
    time.sleep(0.5)

print("\n--- server log (emails) ---")
log = LOG.read_text(errors="replace")
start = log.find("Subject:")
print(log[max(0, start - 200): start + 1800] if start >= 0 else "NO EMAIL FOUND")
print("\nFAILURES:", failures or "none")
