"""Start the dev server, sign in as several demo users and save screenshots."""
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
OUT = PROJECT / "docs" / "img"
OUT.mkdir(parents=True, exist_ok=True)


def reset_demo_database():
    """Start from an empty database with the reference data and the demo users, and no uploaded files."""
    (PROJECT / "db.sqlite3").unlink(missing_ok=True)
    shutil.rmtree(PROJECT / "media", ignore_errors=True)
    subprocess.run([PY, "manage.py", "migrate", "-v", "0"], cwd=PROJECT, check=True)
    subprocess.run([PY, "manage.py", "seed", "--demo"], cwd=PROJECT, check=True, stdout=subprocess.DEVNULL)


reset_demo_database()
subprocess.run(
    [PY, "manage.py", "shell", "-c", "from accounts.models import User; User.objects.create_superuser('admin', 'admin@example.com', 'shot-admin-pass-1')"],
    cwd=PROJECT, check=True,
)
BASE = "http://127.0.0.1:8765"
PASSWORD = "demo-pass-123"
failures = []


def check(label, condition):
    print(("ok   " if condition else "FAIL ") + label)
    if not condition:
        failures.append(label)


server = subprocess.Popen(
    [PY, "manage.py", "runserver", "8765", "--noreload"],
    cwd=PROJECT,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
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

        def new_page(width=1280, height=720):
            ctx = browser.new_context(viewport={"width": width, "height": height})
            return ctx, ctx.new_page()

        def sign_in(page, username, password=PASSWORD):
            page.goto(BASE + "/login/")
            page.fill("input[name=username]", username)
            page.fill("input[name=password]", password)
            page.click("button[type=submit]")
            page.wait_for_load_state()

        def shot(page, name, full=False):
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
            print("saved", name)

        # A non-staff user cannot reach Settings
        ctx, page = new_page(1280, 600)
        sign_in(page, "fa.pic")
        page.goto(BASE + "/admin/")
        check("a PIC is refused at Settings", "not authorized" in page.content())
        shot(page, "06-settings-denied")
        ctx.close()

        # The Coordinator's Settings
        ctx, page = new_page(1280, 800)
        sign_in(page, "mr")
        page.goto(BASE + "/admin/")
        content = page.content()
        check("the Coordinator can maintain the lists", all(name in content for name in ("Complaint types", "Lab tests", "Complaint methods", "Factory PICs")))
        check("the Coordinator cannot edit users", "/admin/accounts/user/" not in content)
        shot(page, "07-settings-home")
        page.goto(BASE + "/admin/complaints/complainttype/")
        check("complaint types list shows the reminder days", "Reminder days" in page.content() and "Packaging" in page.content())
        shot(page, "08-settings-complaint-types")
        page.goto(BASE + "/admin/complaints/complainttype/2/change/")
        shot(page, "09-settings-packaging-subtypes", full=True)
        page.goto(BASE + "/admin/accounts/factorypic/")
        shot(page, "10-settings-factory-pics", full=True)
        page.goto(BASE + "/admin/complaints/labtest/")
        shot(page, "11-settings-lab-tests")

        # The Coordinator changes the Logistic reminder to 5 days, then back to 3
        for days in ("5", "3"):
            page.goto(BASE + "/admin/complaints/complainttype/3/change/")
            page.fill("input[name=reminder_days]", days)
            page.click("input[name=_save]")
            page.wait_for_load_state()
            check(f"reminder days {days} saved", "was changed successfully" in page.content())
            if days == "5":
                shot(page, "12-settings-saved")
        ctx.close()

        # The audit log, which only an administrator can open
        ctx, page = new_page(1280, 800)
        sign_in(page, "admin", "shot-admin-pass-1")
        page.goto(BASE + "/admin/audit/auditlog/")
        check("the audit log records the Coordinator's change", page.locator("table#result_list tbody tr").count() >= 2 and "Sample Coordinator" in page.content())
        shot(page, "13-audit-log")
        page.click("table#result_list tbody tr:nth-child(2) th a")
        page.wait_for_load_state()
        shot(page, "14-audit-entry-detail", full=True)
        ctx.close()

        browser.close()
finally:
    server.terminate()

print("\nFAILURES:", failures or "none")
