"""Step 4 in a real browser: age two complaints, run the reminder job (once with a broken mail server,
then normally), and look at the list, the complaint and the notification log."""
import re
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
BASE = "http://127.0.0.1:8767"
PASSWORD = "demo-pass-123"
LOG = SCRATCH / "server4.log"




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
    [PY, "manage.py", "runserver", "8767", "--noreload"], cwd=PROJECT, env={**os.environ, "AUTO_ASSIGN": "0"}, stdout=open(LOG, "w"), stderr=subprocess.STDOUT,
)
failures = []


def check(label, condition):
    print(("ok   " if condition else "FAIL ") + label)
    if not condition:
        failures.append(label)


def manage(*args, env=None):
    result = subprocess.run(
        [PY, "manage.py", *args], cwd=PROJECT, capture_output=True, text=True, env={**os.environ, **(env or {})}
    )
    return result.stdout + result.stderr


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
            ctx = browser.new_context(viewport={"width": width, "height": height})
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

        def lodge(page, buyer):
            page.goto(BASE + "/complaints/new/")
            page.select_option("select[name=factory]", label="Factory A")
            choose(page, "customer", buyer)
            page.fill("input[name=contract_number]", "SC-000123")
            choose(page, "product", "Sample Product")
            page.fill("input[name=received_date]", ago(5))
            page.select_option("select[name=packing_type]", label="Bulk")
            page.fill("input[name=affected_quantity]", "20")
            page.select_option("select[name=quantity_unit]", label="Metric Ton")
            page.select_option("select[name=transport_type]", label="Local Delivery - Bulk")
            page.fill("input[name=vehicle_number]", "ABC 1234")
            page.fill("input[name=loading_port]", "Example Loading Port")
            page.fill("input[name=discharge_port]", "Example Discharge Port")
            page.fill("input[name=delivery_date]", ago(2))
            page.select_option("select[name=complaint_type]", label="Logistic")
            page.select_option("select[name=complaint_sub_type]", label="Logistic - Shortage")
            page.select_option("select[name=sample_sent]", label="No")
            page.fill("textarea[name=issue_identified]", "Delivery was short by two tons on arrival.")
            page.fill("input[name=contact_email]", "contact@example.com")
            page.click("button[name=submit]")
            page.wait_for_load_state()
            check(f"lodged {buyer}", "was submitted to the Coordinator" in page.content())

        ctx, page = sign_in("sales")
        lodge(page, "Alpha Foods Ltd")
        lodge(page, "Bravo Trading Sdn Bhd")
        ctx.close()

        ctx, page = sign_in("mr")
        page.goto(BASE + "/")
        check("nothing is overdue yet", page.locator(".kpi.bad b").inner_text() == "0")
        page.click("a:has-text('FA/2026/0001')")
        page.click(".next-step a:has-text('Assign PICs')")
        page.get_by_label(re.compile("Production PIC")).check()
        page.click("button:has-text('Assign and send')")
        page.wait_for_load_state()
        check("first complaint assigned", "was assigned" in page.content())
        ctx.close()

        # Age them: the first has waited 4 days for the PIC, the second 5 days for the Coordinator
        manage("shell", "-c", (
            "from django.utils import timezone; from datetime import timedelta; from complaints.models import Complaint;"
            "now = timezone.now();"
            "Complaint.objects.filter(reference='FA/2026/0001').update(status_changed_at=now - timedelta(days=4));"
            "Complaint.objects.filter(reference='FA/2026/0002').update(status_changed_at=now - timedelta(days=5))"
        ))
        dry = manage("send_reminders", "--dry-run")
        print(dry.strip())
        check("dry run lists both", "2 reminder(s) are due." in dry and "would remind" in dry)

        # First the mail server is down, then it is back
        broken = manage("send_reminders", env={"EMAIL_HOST": "127.0.0.1", "EMAIL_PORT": "1"})
        print(broken.strip())
        check("both reminders fail while the mail server is down", "2 could not be sent" in broken and "0 reminder(s) sent." in broken)
        fixed = manage("send_reminders")
        print(fixed.strip())
        check("both are sent when it is back", "2 reminder(s) sent." in fixed)
        again = manage("send_reminders")
        check("nothing is sent twice", "0 reminder(s) sent." in again)

        ctx, page = sign_in("mr")
        page.goto(BASE + "/")
        tiles = page.locator(".kpi b").all_inner_texts()
        check("the Coordinator's tiles show two overdue", tiles[3] == "2")
        check("overdue tags are shown", page.locator(".tag.overdue").count() == 2)
        shot(page, "44-home-overdue", full=False)
        page.goto(BASE + "/?overdue=on")
        check("overdue filter keeps both", page.locator("table.list tbody tr").count() == 2)

        page.goto(BASE + "/notifications/")
        shot(page, "45-notification-log", full=False)
        check("a warning says no mail server is set up, so nothing is delivered", "No mail server is set up on this site" in page.content())
        check("log shows two failed reminders and two that were only logged", page.locator(".status-pending_scar").count() == 2 and page.locator("tr:has-text('Reminder') .status-approved").count() == 2 and "Not delivered" in page.content())
        page.select_option("select[name=result]", label="Failed")
        page.click("button:has-text('Apply')")
        page.wait_for_load_state()
        check("failed filter shows the failures with their reason", page.locator("tbody tr").count() == 2 and "Connection" in page.content())
        shot(page, "46-notification-log-failed", full=False)
        page.goto(BASE + "/notifications/")
        page.locator("details.mail summary").first.click()
        shot(page, "47-notification-email-text", full=False)

        page.goto(BASE + "/")
        page.click("a:has-text('FA/2026/0001')")
        page.wait_for_load_state()
        check("detail says it is overdue", "overdue" in page.locator(".scope").inner_text() and "4 days, overdue" in page.locator(".stepper").inner_text())
        check("detail shows reminder count", "1 reminder sent at this stage" in page.content())
        shot(page, "48-detail-overdue")
        ctx.close()

        ctx, page = sign_in("fa.pic")
        page.goto(BASE + "/")
        check("the PIC sees the overdue complaint as waiting for them", page.locator("strong.mine").count() == 1 and page.locator(".tag.overdue").count() == 2)
        page.goto(BASE + "/notifications/")
        check("a PIC cannot open the log", "/notifications/" not in page.url and "for the Coordinator and top management" in page.content())
        ctx.close()

        ctx, page = sign_in("mr", width=390, height=800)
        page.goto(BASE + "/notifications/")
        check("no sideways scrolling in the log at phone width", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
        shot(page, "49-mobile-notification-log", full=False)
        ctx.close()
        browser.close()
finally:
    server.terminate()
    time.sleep(0.5)

log = LOG.read_text(errors="replace")
subjects = re.findall(r"^Subject: (.*)$", log, flags=re.M)
print("\nsubjects printed by the server (assignment + lodging):", len(subjects))
print("\nFAILURES:", failures or "none")
