# Customer Complaint Platform

Group customer complaint platform for BEST Group's five factories, built with Django from
`docs/FINAL_Customer_Complaint_Platform_Proposal.pdf`. The plan is in `docs/Development_Ideas.md`.
How to test it, by script and by hand, is in **[docs/TestGuide.md](docs/TestGuide.md)**.

## Run it locally

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py seed --demo     # reference data plus fictional demo users
.venv\Scripts\python manage.py runserver
```

Open http://127.0.0.1:8000/. Every demo user has the password `demo-pass-123`:

| Username | Role |
|---|---|
| `mr` | Coordinator (MR). Can open Settings (the admin site) to maintain complaint types, lab tests, complaint methods and each factory's PIC list. |
| `management` | Top management (all factories) |
| `sales` | Lodger outside the factories |
| `fa.fm`, `fa.hod`, `fa.pic`, `fa.lab`, `fa.labmgr` | Factory A staff: FM, HOD, a PIC, a laboratory PIC and the lab manager. The same pattern exists for `fb` to `fe`. |

Create a real administrator with `manage.py createsuperuser`. Run `manage.py seed` (without
`--demo`) on a production install to load the factories, complaint types and lab tests.

## The process

Steps 1 to 8 of the proposal are built: lodging, assignment by the Coordinator, the PIC's investigation
with the laboratory section, SCAR to an external party, HOD review, FM approval (with the rejection loop),
the reply to the customer and the Coordinator's effectiveness check and closing. Every step checks who may
do it and in which status, writes a line in the complaint's history and emails the people concerned.
To try it, sign in as `sales` to lodge a complaint, then as `mr`, `fa.lab`, `fa.pic`, `fa.hod`, `fa.fm`
in turn. In development the emails are printed in the server console.

The top of every complaint shows a progress bar (Submitted, Assignment, Investigation, HOD review, FM approval,
Customer reply, Effectiveness and closing) with the date each stage was done and who the complaint is waiting
for now. When the HOD, FM or Coordinator sends it back, the marker moves back to Investigation and says why.

## Reminders and notifications

A complaint that has waited for the same person for longer than its reminder days (3 unless the Coordinator
changed it for that complaint type in Settings) gets a reminder email, repeated every that-many days until
somebody acts. Run the job once a day:

```powershell
.venv\Scripts\python manage.py send_reminders             # send what is due
.venv\Scripts\python manage.py send_reminders --dry-run   # only list it
```

Schedule it with Windows Task Scheduler (daily, program `.venv\Scripts\python.exe`, arguments
`manage.py send_reminders`, start in the project folder), or with cron on Linux:
`0 8 * * * cd /path/to/project && .venv/bin/python manage.py send_reminders`.
A reminder that cannot be emailed is tried again the next time the job runs.

Every email the platform sends, or fails to send, is written to the notification log. The Coordinator and top
management open it from the menu (Notifications), and each complaint shows its own emails to them.

## Dashboard and complaint log

**Dashboard** (menu: Dashboard) shows the figures of the complaints a user may see, with one filter row for factories and the
lodged period: the summary tiles, complaints by type, where the complaints are in the process, valid complaints by type,
open complaint aging, where each open complaint is waiting and who for, the reasons for invalid and deleted complaints, and the
latest complaints.

**Complaint log** (menu: Complaint log) lists every complaint in the columns of the current log, in the same order, with the
CAR number left out because the complaint reference replaces it. **Export to Excel** downloads the same table as an `.xlsx`
file, followed by the summary and a line saying who exported what. Both follow the user's role and factory, so nobody exports
more than they can see. Drafts are never counted.

## PDF report, audit pack and import of past complaints

**PDF report.** Every complaint has a **PDF report** button: the whole record in one document for filing and for FSSC and ISO
audits, with page numbers and who printed it. **Audit pack** is a zip with that PDF, the SCARs as sent, the reply and every
attached file in a folder for each section. The proposal asks for the layout of the current Customer Complaint Report; that
report was not supplied, so the sections follow what the proposal says it holds (see `complaints/report_pdf.py` to change them).

**Import** (menu, Coordinator only) loads the past 6 months from one spreadsheet. Download the template, fill in one row for
each complaint, upload it, read the check, then confirm. Nothing is saved until every row is correct, and then all rows are
saved together. Each complaint gets a reference in date order; the old number is kept; the stage comes from the dates.
No emails are sent.

## Screenshots

Taken from the running app with the demo data (`docs/img/`, made by `tools/screenshots/`; see the [Test Guide](docs/TestGuide.md#7-the-screenshots)):

| | |
|---|---|
| Sign in, and a failed sign-in | `01-login.png`, `02-login-error.png` |
| The complaint list for a Factory A PIC, a Factory B user and the Coordinator | `03-home-pic.png`, `04-home-other-factory.png`, `05-home-coordinator.png` |
| Settings refused to a non-Coordinator | `06-settings-denied.png` |
| The Coordinator's Settings: home, complaint types, sub-types, factory PICs, lab tests, saving a change | `07` to `12` |
| Audit log and one entry showing who changed what | `13-audit-log.png`, `14-audit-entry-detail.png` |
| Lodging: blank form, saved draft with its reference number, a submit with fields missing, the submitted complaint, the list | `15` to `19` |
| Help page, a filtered list, and the phone-width list and form | `21-help.png`, `22-home-filtered.png`, `23-mobile-list.png`, `24-mobile-form.png` |

| The whole process for one complaint, as each person sees it: Coordinator's list and assignment (a sample needs a lab PIC), laboratory section, incomplete findings, SCAR, pending SCAR, returned SCAR, HOD review and return, FM rejection and approval, reply to the customer, closing, the finished record with its history, and the phone view | `25` to `43` |
| Dashboard, the complaint log and its Excel file, with the filter row and the phone view | `50` to `56` |
| PDF report, audit pack and the import of past complaints: the Import page, a file with mistakes, the corrected file, the result, an imported complaint, three pages of the PDF report, the phone view | `57` to `66` |
| Reminders and notifications: the list with the Overdue tile and day counts, the log (sent and failed reminders, the failed ones with their reason, one email opened), an overdue complaint with its notifications, the phone view | `44` to `49` |

The settings and audit-log pages (`06` to `14`) are Django's standard admin site and are not
restyled. Everything else uses the shared layout in `templates/base.html` and
`static/css/app.css`; the colours are variables at the top of that file.

## Testing

```powershell
.venv\Scripts\python -m pytest
```

All 227 automated tests should pass. The **[Test Guide](docs/TestGuide.md)** explains what each test file covers, gives a
manual test script for every step of the process (with the screenshot of what you should see), and says how the images in
`docs/img/` are regenerated (`python tools/screenshots/run_all.py`).

## Settings by environment variable

`DJANGO_DEBUG`, `DJANGO_SECRET_KEY` (required when debug is off), `DJANGO_ALLOWED_HOSTS`,
`POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_HOST` / `POSTGRES_PORT`,
`EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` / `EMAIL_USE_TLS`,
`DEFAULT_FROM_EMAIL`. Without `EMAIL_HOST`, emails are printed to the console.
`SITE_URL` is the address people use to reach the platform; it builds the links in emails
(default `http://127.0.0.1:8000`). `ATTACHMENT_MAX_MB` limits each uploaded file (default 10).

Uploaded files are stored under `media/` and are served only through the platform, which checks
that the user may see the complaint. Do not expose `media/` directly from the web server.
