"""Walk one complaint through the whole process (steps 1 to 8) in a real browser and save screenshots."""
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
BASE = "http://127.0.0.1:8766"
PASSWORD = "demo-pass-123"
LOG = SCRATCH / "server3.log"

(SCRATCH / "leak-photo.png").write_bytes(
    bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")
)
(SCRATCH / "returned-scar.pdf").write_bytes(b"%PDF-1.4\n%returned SCAR for the demo\n")
(SCRATCH / "lab-report.pdf").write_bytes(b"%PDF-1.4\n%lab report for the demo\n")

for name in ("db.sqlite3",):
    (PROJECT / name).unlink(missing_ok=True)


def reset_demo_database():
    """Start from an empty database with the reference data and the demo users, and no uploaded files."""
    (PROJECT / "db.sqlite3").unlink(missing_ok=True)
    shutil.rmtree(PROJECT / "media", ignore_errors=True)
    subprocess.run([PY, "manage.py", "migrate", "-v", "0"], cwd=PROJECT, check=True)
    subprocess.run([PY, "manage.py", "seed", "--demo"], cwd=PROJECT, check=True, stdout=subprocess.DEVNULL)


reset_demo_database()

server = subprocess.Popen(
    [PY, "manage.py", "runserver", "8766", "--noreload"], cwd=PROJECT, stdout=open(LOG, "w"), stderr=subprocess.STDOUT,
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

        def sign_in(username):
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
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

        def status(page):
            return page.locator(".title-row .status, .head-title .status").first.inner_text().strip()

        # 1-2. The lodger completes and submits a complaint with a sample sent
        ctx, page = sign_in("sales")
        page.goto(BASE + "/complaints/new/")
        page.select_option("select[name=factory]", label="Factory A")
        page.fill("input[name=buyer]", "Example Buyer Ltd")
        page.fill("input[name=contract_number]", "SC-000123")
        page.fill("input[name=product_name]", "Sample Product")
        page.fill("input[name=delivery_date]", "2026-10-28")
        page.select_option("select[name=packing_type]", label="Pack")
        page.fill("input[name=pack_size]", "Flexibag")
        page.fill("input[name=affected_quantity]", "20")
        page.select_option("select[name=quantity_unit]", label="Metric Ton")
        page.select_option("select[name=transport_type]", label="Shipment - Container")
        page.fill("input[name=vessel_name]", "MV Example")
        page.fill("input[name=container_number]", "ABCD1234567")
        page.fill("input[name=loading_port]", "Example Loading Port")
        page.fill("input[name=discharge_port]", "Example Discharge Port")
        page.select_option("select[name=complaint_type]", label="Packaging")
        page.select_option("select[name=complaint_sub_type]", label="Packaging - Leaking")
        page.select_option("select[name=sample_sent]", label="Yes")
        page.fill("textarea[name=description]", "Flexibag leaked during discharge; customer reports product loss at the receiving terminal.")
        page.fill("input[name=contact_person]", "Sample Contact")
        page.fill("input[name=contact_email]", "contact@example.com")
        page.select_option("select[name=complaint_method]", label="Email")
        page.set_input_files("input[name=attachments]", [str(SCRATCH / "leak-photo.png")])
        page.click("button[name=submit]")
        page.wait_for_load_state()
        check("lodged and submitted", "was submitted to the Coordinator" in page.content())
        detail = page.url
        pk = re.search(r"/complaints/(\d+)/", detail).group(1)
        check("lodger has nothing to do after submitting", page.locator(".next-step").count() == 0)
        ctx.close()

        # 3. The Coordinator assigns the PIC and the laboratory PIC
        ctx, page = sign_in("mr")
        page.goto(BASE + "/")
        check("Coordinator's tile counts the waiting complaint", page.locator(".kpi.info b").inner_text() == "1")
        shot(page, "25-home-coordinator-waiting", full=False)
        page.click("text=FA/2026/0001")
        page.wait_for_load_state()
        check("detail offers Assign PICs", page.locator(".next-step a:has-text('Assign PICs')").count() == 1)
        shot(page, "26-detail-submitted")
        page.click(".next-step a:has-text('Assign PICs')")
        page.wait_for_load_state()
        page.get_by_label(re.compile("Production PIC")).check()
        page.fill("textarea[name=remarks]", "Please investigate and check the retained sample.")
        page.click("button:has-text('Assign and send')")
        page.wait_for_load_state()
        check("a laboratory PIC is required for a sample complaint", "choose at least one laboratory PIC" in page.content())
        shot(page, "27-assign-needs-lab")
        page.get_by_label(re.compile("Lab PIC")).check()
        page.click("button:has-text('Assign and send')")
        page.wait_for_load_state()
        check("assigned", "was assigned" in page.content() and status(page) == "PIC investigation")
        shot(page, "28-detail-assigned")
        ctx.close()

        # 4. Laboratory staff fill in their section
        ctx, page = sign_in("fa.lab")
        page.goto(BASE + f"/complaints/{pk}/")
        check("lab staff see the laboratory action", page.locator(".next-step a:has-text('Laboratory section')").count() == 1)
        page.goto(BASE + f"/complaints/{pk}/laboratory/")
        page.fill("input[name=acknowledged_on]", "2026-11-02")
        inputs = page.locator("td.cell-input input")
        inputs.nth(0).fill("0.42%")
        inputs.nth(1).fill("0.44%")
        inputs.nth(2).fill("2.1")
        inputs.nth(3).fill("2.0")
        page.fill("textarea[name=remarks]", "Both samples are within specification except for FFA.")
        page.set_input_files("input[name=attachments]", [str(SCRATCH / "lab-report.pdf")])
        shot(page, "29-lab-section")
        page.click("button:has-text('Save laboratory section')")
        page.wait_for_load_state()
        check("laboratory section saved", "laboratory section is saved" in page.content())
        ctx.close()

        # 5. The PIC investigates, tries to submit too early, then issues a SCAR
        ctx, page = sign_in("fa.pic")
        page.goto(BASE + "/")
        check("PIC's list marks the complaint as waiting for them", page.locator("strong.mine").count() == 1)
        page.goto(BASE + f"/complaints/{pk}/investigation/")
        page.fill("textarea[name=investigation_summary]", "We reviewed the packing records, the supplier's weld test report and the retained sample.")
        page.fill("textarea[name=root_cause]", "The flexibag seal failed because of a defective weld at the supplier.")
        page.fill("textarea[name=conclusion]", "The leak was caused by the supplier's weld, not by our filling or loading.")
        page.click("button[name=submit]")
        page.wait_for_load_state()
        check("early submit lists what is missing", "Enter the correction" in page.content() and "Your changes are saved" in page.content())
        shot(page, "30-investigation-incomplete")
        page.fill("textarea[name=correction]", "Replaced the affected flexibags and repacked the shipment.")
        page.fill("textarea[name=corrective_action]", "Audit the supplier's welding process and add a seal pressure test before filling.")
        page.select_option("select[name=cause]", label="External (supplier, transporter, other party)")
        page.click("button[name=save]")
        page.wait_for_load_state()
        page.click("a:has-text('Issue SCAR')")
        page.wait_for_load_state()
        page.select_option("select[name=party_type]", label="Supplier")
        page.fill("input[name=party_name]", "Flexibag Supplies Co.")
        page.fill("input[name=contact_email]", "quality@flexibag.example")
        page.click("button:has-text('Generate again')")
        page.wait_for_load_state()
        check("generate again rebuilds the wording for the party", "Dear Flexibag Supplies Co.," in page.input_value("textarea[name=message]"))
        check("generate again keeps who it is for", page.input_value("input[name=contact_email]") == "quality@flexibag.example")
        shot(page, "31-scar")
        page.click("button:has-text('Send SCAR')")
        page.wait_for_load_state()
        check("SCAR sent and status is Pending SCAR", status(page) == "Pending SCAR from Supplier")
        shot(page, "32-detail-pending-scar")
        page.goto(BASE + f"/complaints/{pk}/investigation/")
        check("Submit to HOD is locked", page.locator("button:has-text('Submit to HOD (locked)')").is_disabled())
        page.goto(BASE + f"/complaints/{pk}/scar/return/")
        page.set_input_files("input[name=file]", [str(SCRATCH / "returned-scar.pdf")])
        page.click("button:has-text('Upload returned SCAR')")
        page.wait_for_load_state()
        check("returned SCAR unlocks the findings", "returned SCAR is uploaded" in page.content() and status(page) == "PIC investigation")
        shot(page, "33-investigation-ready")
        page.click("button[name=submit]")
        page.wait_for_load_state()
        check("submitted to the HOD", status(page) == "HOD review")
        ctx.close()

        # 6. HOD returns once, then submits to the FM
        ctx, page = sign_in("fa.hod")
        page.goto(BASE + f"/complaints/{pk}/hod/")
        shot(page, "34-hod-review")
        page.fill("textarea[name=comment]", "Please add the supplier's contact for the audit date.")
        page.click("button[name=return]")
        page.wait_for_load_state()
        check("HOD returned it to the PIC", status(page) == "PIC investigation")
        ctx.close()
        ctx, page = sign_in("fa.pic")
        page.goto(BASE + f"/complaints/{pk}/investigation/")
        check("PIC sees the HOD's comment", "supplier's contact for the audit date" in page.content())
        shot(page, "35-investigation-returned")
        page.fill("textarea[name=corrective_action]", "Audit the supplier's welding process (audit date to be agreed) and add a seal pressure test before filling.")
        page.click("button[name=submit]")
        page.wait_for_load_state()
        check("resubmitted to the HOD", status(page) == "HOD review")
        ctx.close()
        ctx, page = sign_in("fa.hod")
        page.goto(BASE + f"/complaints/{pk}/hod/")
        page.check("input[name=decision][value=valid]")
        page.select_option("select[name=cause]", label="External (supplier, transporter, other party)")
        page.click("button[name=submit]")
        page.wait_for_load_state()
        check("HOD submitted to the FM", status(page) == "FM approval")
        shot(page, "36-detail-fm-review")
        ctx.close()

        # 7. The FM rejects without a comment (refused), rejects with one, then approves the second time
        ctx, page = sign_in("fa.fm")
        page.goto(BASE + f"/complaints/{pk}/fm/")
        page.click("button[name=reject]")
        page.wait_for_load_state()
        check("rejection needs a comment", "A comment is needed to reject" in page.content())
        shot(page, "37-fm-needs-comment")
        page.fill("textarea[name=comment]", "Corrective action needs a date for the supplier audit.")
        page.click("button[name=reject]")
        page.wait_for_load_state()
        check("FM rejected", status(page) == "PIC investigation")
        ctx.close()
        ctx, page = sign_in("fa.pic")
        page.goto(BASE + f"/complaints/{pk}/investigation/")
        page.fill("textarea[name=corrective_action]", "Audit the supplier's welding process by 30 Nov 2026 and add a seal pressure test before filling.")
        page.click("button[name=submit]")
        page.wait_for_load_state()
        ctx.close()
        ctx, page = sign_in("fa.hod")
        page.goto(BASE + f"/complaints/{pk}/hod/")
        page.click("button[name=submit]")
        page.wait_for_load_state()
        check("second submission to the FM", status(page) == "FM approval")
        ctx.close()
        ctx, page = sign_in("fa.fm")
        page.goto(BASE + f"/complaints/{pk}/fm/")
        shot(page, "38-fm-approval")
        page.fill("textarea[name=comment]", "Approved. Thank you for the quick turnaround.")
        page.click("button[name=approve]")
        page.wait_for_load_state()
        check("FM approved", status(page) == "Approved, reply due")
        ctx.close()

        # 8. The PIC replies to the customer
        ctx, page = sign_in("fa.pic")
        page.goto(BASE + f"/complaints/{pk}/reply/")
        body = page.input_value("textarea[name=body]")
        check("the reply subject follows the template", page.input_value("input[name=subject]").startswith("Investigation Findings for Complaint FA/2026/0001"))
        check("the reply follows the template", all(part in body for part in ("Dear Sample Contact,", "Investigation Summary", "Root Cause Assessment", "Conclusion", "Corrective and Preventive Actions", "Yours sincerely,", "Sample FM FA", "Factory Manager, Factory A", "BEST Group")) and "seal failed" in body.lower())
        shot(page, "39-reply")
        page.click("button:has-text('Send to customer')")
        page.wait_for_load_state()
        check("reply sent", status(page) == "Customer Response Sent")
        ctx.close()

        # 9. The Coordinator checks effectiveness and closes the case
        ctx, page = sign_in("mr")
        page.goto(BASE + f"/complaints/{pk}/verify/")
        page.click("button[name=close]")
        page.wait_for_load_state()
        check("closing needs the first check", "needed to close the case" in page.content())
        page.fill("input[name=first_verified_date]", "2026-12-15")
        page.fill("textarea[name=first_result]", "No repeat of the leak in the next two shipments.")
        page.fill("input[name=second_verified_date]", "2027-03-15")
        page.fill("textarea[name=second_result]", "Supplier audit completed; pressure test in use.")
        page.fill("textarea[name=comment]", "Corrective action effective.")
        shot(page, "40-verify")
        page.click("button[name=close]")
        page.wait_for_load_state()
        check("case closed", status(page) == "Closed")
        shot(page, "41-detail-closed")
        page.goto(BASE + "/")
        shot(page, "42-home-closed", full=False)
        ctx.close()

        # Management sees it read-only; another factory does not
        ctx, page = sign_in("management")
        page.goto(BASE + f"/complaints/{pk}/")
        check("top management sees the closed case with no actions", page.locator(".next-step").count() == 0)
        ctx.close()
        ctx, page = sign_in("fb.hod")
        resp = page.goto(BASE + f"/complaints/{pk}/")
        check("another factory's HOD gets a not-found page", resp.status == 404)
        ctx.close()

        # Phone width for the new screens
        ctx = browser.new_context(viewport={"width": 390, "height": 800})
        page = ctx.new_page()
        page.goto(BASE + "/login/")
        page.fill("input[name=username]", "mr")
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_load_state()
        page.goto(BASE + f"/complaints/{pk}/")
        check("no sideways scrolling on the detail page at phone width", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
        shot(page, "43-mobile-detail", full=False)
        ctx.close()
        browser.close()
finally:
    server.terminate()
    time.sleep(0.5)

log = LOG.read_text(errors="replace")
subjects = re.findall(r"^Subject: (.*)$", log, flags=re.M)
print("\n--- emails sent (%d) ---" % len(subjects))
for s in subjects:
    print(" ", s)
print("\nFAILURES:", failures or "none")
