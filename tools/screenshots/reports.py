"""Step 5 in a real browser: the dashboard, the complaint log and the Excel export, with twelve complaints."""
import io
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from openpyxl import load_workbook
from playwright.sync_api import sync_playwright

PROJECT = Path(__file__).resolve().parents[2]
PY = str(next(p for p in (PROJECT / ".venv" / "Scripts" / "python.exe", PROJECT / ".venv" / "bin" / "python") if p.exists()))
SCRATCH = Path(tempfile.gettempdir()) / "complaint-screenshots"
SCRATCH.mkdir(exist_ok=True)
OUT = PROJECT / "docs" / "img"
BASE = "http://127.0.0.1:8768"
PASSWORD = "demo-pass-123"
LOG = SCRATCH / "server5.log"

def reset_demo_database():
    """Start from an empty database with the reference data and the demo users, and no uploaded files."""
    (PROJECT / "db.sqlite3").unlink(missing_ok=True)
    shutil.rmtree(PROJECT / "media", ignore_errors=True)
    subprocess.run([PY, "manage.py", "migrate", "-v", "0"], cwd=PROJECT, check=True)
    subprocess.run([PY, "manage.py", "seed", "--demo"], cwd=PROJECT, check=True, stdout=subprocess.DEVNULL)


reset_demo_database()
made = subprocess.run(  # twelve fictional complaints in every state, made with the same code the screens use
    [PY, "manage.py", "shell"], cwd=PROJECT, input=(Path(__file__).parent / "demo_data.py").read_text(encoding="utf-8"),
    capture_output=True, text=True,
)
print((made.stdout + made.stderr).strip().splitlines()[-1])

server = subprocess.Popen(
    [PY, "manage.py", "runserver", "8768", "--noreload"], cwd=PROJECT, stdout=open(LOG, "w"), stderr=subprocess.STDOUT,
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

        def sign_in(username, width=1440, height=900, scheme="light"):
            ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme, accept_downloads=True)
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

        def tiles(page):
            return {label: int(value) for value, label in re.findall(r"<b>(\d+)</b><span>([^<]+)</span>", page.content())}

        ctx, page = sign_in("management")
        page.goto(BASE + "/dashboard/")
        t = tiles(page)
        check("twelve complaints in total", t["Total number of complaints"] == 12)
        check("the four classes add up to the total", t["Valid"] + t["Invalid"] + t["Under investigation"] + t["Deleted"] == 12)
        check("valid is split into in progress and closed", t["Valid complaints in progress"] + t["Valid complaints closed"] == t["Valid"] and t["Valid complaints closed"] == 2)
        check("one deleted", t["Deleted"] == 1)
        check("filter row has five factories", page.locator(".chips input[type=checkbox]").count() == 5)
        check("aging bars use the ordered ramp", page.locator(".bars.ordinal .bar").count() >= 3)
        shot(page, "50-dashboard")

        # Filter: Factory A and B only
        page.get_by_label("Factory A").check()
        page.get_by_label("Factory B").check()
        page.click("button:has-text('Apply')")
        page.wait_for_load_state()
        t = tiles(page)
        check("factory filter narrows the totals to A and B", t["Total number of complaints"] == 6)
        check("showing line names the factories", "Factory A, Factory B" in page.locator(".scope").inner_text())
        shot(page, "51-dashboard-filtered", full=False)

        # The complaint log
        page.goto(BASE + "/log/")
        headers = [h.strip().lower() for h in page.locator("table.log thead th").all_inner_texts()]
        check("log columns are those of the current log", headers[1] == "customer complaint no." and headers[-1] == "reason delete" and not any("car" in h.split() for h in headers))
        check("log lists twelve rows", page.locator("table.log tbody tr").count() == 12)
        shot(page, "52-complaint-log", full=False)
        page.evaluate("document.querySelector('.log-wrap').scrollTo(0, 0); document.querySelector('.log-wrap').scrollLeft = 1500")
        shot(page, "53-complaint-log-scrolled", full=False)

        # Excel export, with the Factory A + B filter
        page.goto(BASE + "/log/")
        with page.expect_download() as download:
            page.click("a:has-text('Export to Excel')")
        path = SCRATCH / "exported-log.xlsx"
        download.value.save_as(str(path))
        check("download is named for today", re.fullmatch(r"complaint-log-\d{4}-\d{2}-\d{2}\.xlsx", download.value.suggested_filename) is not None)
        sheet = load_workbook(path)["Complaint log"]
        check("Excel has the log headers and twelve rows", sheet.cell(1, 2).value == "Customer complaint no." and sheet.cell(13, 2).value and sheet.cell(14, 2).value is None)
        check("Excel keeps real dates", sheet.cell(2, 5).is_date)
        print("excel summary:", [(sheet.cell(r, 2).value, sheet.cell(r, 3).value) for r in range(15, 23)])
        ctx.close()

        # A factory user sees only their factory
        ctx, page = sign_in("fa.pic")
        page.goto(BASE + "/dashboard/")
        t = tiles(page)
        check("a Factory A PIC's dashboard counts four complaints", t["Total number of complaints"] == 4)
        check("a factory user has no choice of other factories", page.locator(".chips input[type=checkbox]").count() == 1)
        ctx.close()

        # Dark mode
        ctx, page = sign_in("mr", scheme="dark")
        page.goto(BASE + "/dashboard/")
        shot(page, "54-dashboard-dark", full=False)
        ctx.close()

        # Phone width
        ctx, page = sign_in("mr", width=390, height=800)
        page.goto(BASE + "/dashboard/")
        check("no sideways scrolling on the dashboard at phone width", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
        shot(page, "55-mobile-dashboard", full=False)
        page.goto(BASE + "/log/")
        check("no sideways scrolling on the log at phone width", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
        shot(page, "56-mobile-log", full=False)
        ctx.close()
        browser.close()
finally:
    server.terminate()
    time.sleep(0.5)

print("\nFAILURES:", failures or "none")
