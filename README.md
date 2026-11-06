# Customer Complaint Platform

Group customer complaint platform for BEST Group's five factories, built with Django from
`docs/FINAL_Customer_Complaint_Platform_Proposal.pdf`. The plan is in `docs/Development_Ideas.md`.

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
| `fa.fm`, `fa.hod`, `fa.pic`, `fa.lab`, `fa.labmgr` | Factory A staff. The same pattern exists for `fb` to `fe`. |

Create a real administrator with `manage.py createsuperuser`. Run `manage.py seed` (without
`--demo`) on a production install to load the factories, complaint types and lab tests.

## Screenshots

Taken from the running app with the demo data (`docs/img/`):

| | |
|---|---|
| Sign in, and a failed sign-in | `01-login.png`, `02-login-error.png` |
| Home page for a PIC, a lodger and the Coordinator | `03-home-pic.png`, `04-home-lodger.png`, `05-home-coordinator.png` |
| Settings refused to a non-Coordinator | `06-settings-denied.png` |
| The Coordinator's Settings: home, complaint types, sub-types, factory PICs, lab tests, saving a change | `07` to `12` |
| Audit log and one entry showing who changed what | `13-audit-log.png`, `14-audit-entry-detail.png` |

## Tests

```powershell
.venv\Scripts\python -m pytest
```

## Settings by environment variable

`DJANGO_DEBUG`, `DJANGO_SECRET_KEY` (required when debug is off), `DJANGO_ALLOWED_HOSTS`,
`POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_HOST` / `POSTGRES_PORT`,
`EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` / `EMAIL_USE_TLS`,
`DEFAULT_FROM_EMAIL`. Without `EMAIL_HOST`, emails are printed to the console.
