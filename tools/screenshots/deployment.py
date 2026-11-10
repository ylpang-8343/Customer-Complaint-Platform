"""The demo-site features: the sign-in page that lists the demo users, and sending due reminders from the page."""
import os
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
BASE = "http://127.0.0.1:8771"
PASSWORD = "demo-pass-123"
LOG = SCRATCH / "server7.log"


def reset_demo_database():
    """Start from an empty database with the reference data, the demo users and the demo complaints."""
    (PROJECT / "db.sqlite3").unlink(missing_ok=True)
    shutil.rmtree(PROJECT / "media", ignore_errors=True)
    subprocess.run([PY, "manage.py", "migrate", "-v", "0"], cwd=PROJECT, check=True)
    subprocess.run([PY, "manage.py", "seed", "--demo", "--complaints"], cwd=PROJECT, check=True, stdout=subprocess.DEVNULL)


reset_demo_database()
server = subprocess.Popen(
    [PY, "manage.py", "runserver", "8771", "--noreload"], cwd=PROJECT, env={**os.environ, "DEMO_MODE": "true"},
    stdout=open(LOG, "w"), stderr=subprocess.STDOUT,
)
failures = []


def check(label, condition):
    print(("ok   " if condition else "FAIL ") + label)
    if not condition:
        failures.append(label)


try:
    for _ in range(40):
        try:
            urllib.request.urlopen(BASE + "/healthz/", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    else:
        sys.exit("server did not start")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")

        def shot(page, name, full=False):
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
            print("saved", name)

        ctx = browser.new_context(viewport={"width": 1280, "height": 1000})
        page = ctx.new_page()
        check("the health check answers", page.goto(BASE + "/healthz/").status == 200 and page.inner_text("body") == "ok")
        page.goto(BASE + "/login/")
        check("the sign-in page lists the demo users and the password", "Demo site" in page.content() and PASSWORD in page.content() and page.locator(".demo-card code").count() >= 7)
        shot(page, "67-login-demo")
        page.fill("input[name=username]", "mr")
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_load_state()
        page.goto(BASE + "/notifications/")
        due = int(re.search(r"<strong>(\d+)</strong> reminders? (?:is|are) due now", page.content()).group(1))
        check("reminders are due on the demo data", due > 0)
        shot(page, "68-notifications-send-reminders")
        page.click("button:has-text('Send due reminders now')")
        page.wait_for_load_state()
        check("the button sends them", f"{due} reminder" in page.content() and "sent." in page.content())
        check("none are due afterwards", "No reminders are due" not in page.content() and re.search(r"<strong>0</strong> reminders are due now", page.content()) is not None)
        shot(page, "69-notifications-reminders-sent")
        ctx.close()

        ctx = browser.new_context(viewport={"width": 390, "height": 900})
        page = ctx.new_page()
        page.goto(BASE + "/login/")
        check("no sideways scrolling on the demo sign-in page at phone width", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
        shot(page, "70-mobile-login-demo", full=True)
        ctx.close()
        browser.close()
finally:
    server.terminate()
    time.sleep(0.5)

print("\nFAILURES:", failures or "none")
