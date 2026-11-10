"""Regenerate every screenshot in docs/img by driving the real app in Chrome, and check what each page shows.

    python tools/screenshots/run_all.py

Needs, in the Python that runs it (not the project's own environment): ``pip install playwright openpyxl pypdfium2 pillow`` and
Google Chrome installed. The app itself is started from the project's .venv. Every script starts from a fresh
demo database, so this erases db.sqlite3 and media/ in the project folder; the demo data is put back at the end.
A script that finds something wrong prints FAIL and the runner exits with a non-zero code.
"""

import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
SCRIPTS = [
    ("lodging.py", "Sign in, lists, lodging, help (01-05, 15-24)"),
    ("admin_screens.py", "Settings and audit log (06-14)"),
    ("process.py", "One complaint through steps 1 to 8 (25-43)"),
    ("reminders.py", "Reminders and notifications (44-49)"),
    ("reports.py", "Dashboard, complaint log, Excel export (50-56)"),
    ("documents.py", "Import of past complaints, PDF report, audit pack (57-66)"),
    ("deployment.py", "Demo site sign-in page, send reminders from the page (67-70)"),
]


def main():
    started, problems = time.time(), []
    for name, what in SCRIPTS:
        print(f"\n=== {name}: {what}", flush=True)
        run = subprocess.run([sys.executable, str(HERE / name)], cwd=PROJECT, capture_output=True, text=True)
        output = run.stdout + run.stderr
        saved = len(re.findall(r"^saved ", output, flags=re.M))
        checks = len(re.findall(r"^ok   ", output, flags=re.M))
        failed = re.findall(r"^FAIL (.*)$", output, flags=re.M)
        print(f"    {saved} screenshots saved, {checks} checks passed, {len(failed)} failed")
        for line in failed:
            print(f"    FAIL {line}")
        if run.returncode != 0:
            tail = "\n".join(output.strip().splitlines()[-12:])
            print(f"    the script stopped with an error:\n{tail}")
            problems.append(name)
        elif failed:
            problems.append(name)
    # Leave the project with a clean demo database again.
    subprocess.run([sys.executable, "-c", _RESET], cwd=PROJECT, check=True)
    print(f"\nDone in {time.time() - started:.0f} s. " + ("Problems in: " + ", ".join(problems) if problems else "No problems."))
    return 1 if problems else 0


_RESET = """
import shutil, subprocess, sys
from pathlib import Path
project = Path.cwd()
py = next(str(p) for p in (project / '.venv' / 'Scripts' / 'python.exe', project / '.venv' / 'bin' / 'python') if p.exists())
(project / 'db.sqlite3').unlink(missing_ok=True)
shutil.rmtree(project / 'media', ignore_errors=True)
subprocess.run([py, 'manage.py', 'migrate', '-v', '0'], check=True)
subprocess.run([py, 'manage.py', 'seed', '--demo'], check=True, stdout=subprocess.DEVNULL)
"""

if __name__ == "__main__":
    sys.exit(main())
