"""Putting the platform on a host: settings from the environment, the demo site, and the Render files."""

import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core import mail
from django.core.management import CommandError, call_command
from django.urls import reverse
from django.utils import timezone

from complaints import reporting
from complaints.models import Complaint, NotificationLog
from tests.test_complaints import as_user  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_ENV = (
    "DJANGO_DEBUG", "DJANGO_SECRET_KEY", "DJANGO_ALLOWED_HOSTS", "DJANGO_CSRF_TRUSTED_ORIGINS", "DJANGO_SSL_REDIRECT",
    "DJANGO_HSTS_SECONDS", "DATABASE_URL", "POSTGRES_DB", "SITE_URL", "RENDER_EXTERNAL_HOSTNAME", "RENDER_EXTERNAL_URL",
    "DEMO_MODE", "EMAIL_HOST", "EMAIL_BACKEND", "BREVO_API_KEY", "RESEND_API_KEY", "SENDGRID_API_KEY", "MAILGUN_API_KEY",
    "POSTMARK_SERVER_TOKEN", "COMPANY_NAME",
)  # fmt: skip


def read_settings(**env):
    """Import the settings module in a fresh process with exactly this environment, and return what matters."""
    clean = {k: v for k, v in os.environ.items() if k not in SETTINGS_ENV}
    code = (
        "import json; from config import settings as s;"
        "print(json.dumps({k: getattr(s, k, None) for k in ['DEBUG', 'ALLOWED_HOSTS', 'CSRF_TRUSTED_ORIGINS', 'SITE_URL', "
        "'DEMO_MODE', 'SECURE_SSL_REDIRECT', 'SESSION_COOKIE_SECURE', 'CSRF_COOKIE_SECURE', 'SECURE_HSTS_SECONDS', "
        "'SECURE_PROXY_SSL_HEADER', 'SECURE_REDIRECT_EXEMPT', 'MIDDLEWARE', 'STORAGES', 'EMAIL_BACKEND', 'ANYMAIL', 'DEFAULT_FROM_EMAIL', 'COMPANY_NAME']} | "
        "{'DATABASE': s.DATABASES['default']}, default=str))"
    )
    run = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**clean, **env}, capture_output=True, text=True)
    if run.returncode:
        raise RuntimeError(run.stderr.strip().splitlines()[-1])
    return json.loads(run.stdout)


PRODUCTION = {"DJANGO_DEBUG": "false", "DJANGO_SECRET_KEY": "x" * 60}


# --- Settings -------------------------------------------------------------------------------------------------


def test_development_defaults_need_nothing_set():
    s = read_settings()
    assert s["DEBUG"] is True and s["DATABASE"]["ENGINE"].endswith("sqlite3")
    assert s["EMAIL_BACKEND"].endswith("console.EmailBackend") and s["DEMO_MODE"] is False
    assert not s["SECURE_SSL_REDIRECT"] and not s["SESSION_COOKIE_SECURE"]
    assert s["STORAGES"]["staticfiles"]["BACKEND"] == "django.contrib.staticfiles.storage.StaticFilesStorage"
    assert "whitenoise.middleware.WhiteNoiseMiddleware" not in s["MIDDLEWARE"]


def test_production_needs_a_secret_key():
    with pytest.raises(RuntimeError, match="DJANGO_SECRET_KEY must be set"):
        read_settings(DJANGO_DEBUG="false")


def test_production_turns_on_https_protection_and_static_files():
    s = read_settings(**PRODUCTION)
    assert s["DEBUG"] is False and s["SECURE_SSL_REDIRECT"] is True
    assert s["SESSION_COOKIE_SECURE"] is True and s["CSRF_COOKIE_SECURE"] is True
    assert s["SECURE_PROXY_SSL_HEADER"] == ["HTTP_X_FORWARDED_PROTO", "https"]
    assert s["SECURE_REDIRECT_EXEMPT"] == [r"^healthz/$"] and s["SECURE_HSTS_SECONDS"] == 0
    assert "whitenoise.middleware.WhiteNoiseMiddleware" in s["MIDDLEWARE"]
    assert s["MIDDLEWARE"].index("whitenoise.middleware.WhiteNoiseMiddleware") == 1  # right after SecurityMiddleware
    assert s["STORAGES"]["staticfiles"]["BACKEND"].endswith("CompressedManifestStaticFilesStorage")


def test_https_redirect_and_hsts_can_be_set_from_the_environment():
    s = read_settings(**PRODUCTION, DJANGO_SSL_REDIRECT="false", DJANGO_HSTS_SECONDS="31536000")
    assert s["SECURE_SSL_REDIRECT"] is False and s["SECURE_HSTS_SECONDS"] == 31536000


def test_database_url_selects_postgresql():
    s = read_settings(**PRODUCTION, DATABASE_URL="postgresql://complaints:secret@db.example.com:5432/complaints")
    db = s["DATABASE"]
    assert db["ENGINE"] == "django.db.backends.postgresql"
    assert (db["NAME"], db["USER"], db["PASSWORD"], db["HOST"], str(db["PORT"])) == ("complaints", "complaints", "secret", "db.example.com", "5432")
    assert db["CONN_MAX_AGE"] == 60


def test_the_older_postgres_variables_still_work():
    s = read_settings(**PRODUCTION, POSTGRES_DB="complaints", POSTGRES_USER="u", POSTGRES_HOST="h")
    assert s["DATABASE"]["ENGINE"] == "django.db.backends.postgresql" and s["DATABASE"]["HOST"] == "h"


def test_render_tells_the_app_its_own_address():
    s = read_settings(**PRODUCTION, RENDER_EXTERNAL_HOSTNAME="complaints.onrender.com", RENDER_EXTERNAL_URL="https://complaints.onrender.com")
    assert "complaints.onrender.com" in s["ALLOWED_HOSTS"]
    assert "https://complaints.onrender.com" in s["CSRF_TRUSTED_ORIGINS"]
    assert s["SITE_URL"] == "https://complaints.onrender.com"  # the links in emails
    explicit = read_settings(**PRODUCTION, RENDER_EXTERNAL_URL="https://a.onrender.com", SITE_URL="https://complaints.example.com/")
    assert explicit["SITE_URL"] == "https://complaints.example.com"


def test_hosts_and_trusted_origins_can_be_listed():
    s = read_settings(**PRODUCTION, DJANGO_ALLOWED_HOSTS="a.example.com,b.example.com", DJANGO_CSRF_TRUSTED_ORIGINS="https://a.example.com")
    assert s["ALLOWED_HOSTS"] == ["a.example.com", "b.example.com"] and s["CSRF_TRUSTED_ORIGINS"] == ["https://a.example.com"]


def test_demo_mode_is_off_unless_asked_for():
    assert read_settings(**PRODUCTION)["DEMO_MODE"] is False
    assert read_settings(**PRODUCTION, DEMO_MODE="true")["DEMO_MODE"] is True


# --- The health check and the demo sign-in page --------------------------------------------------------------------


def test_the_health_check_answers_without_signing_in(client, db):
    response = client.get("/healthz/")
    assert response.status_code == 200 and response.content == b"ok"


def test_the_demo_sign_in_page_lists_the_demo_users(client, db, settings):
    assert "Demo site" not in client.get(reverse("login")).content.decode()
    settings.DEMO_MODE = True
    html = client.get(reverse("login")).content.decode()
    assert "Demo site" in html and "demo-pass-123" in html
    for username in ("sales", "mr", "fa.pic", "fa.lab", "fa.hod", "fa.fm", "management"):
        assert f"<code>{username}</code>" in html


# --- Demo data -----------------------------------------------------------------------------------------------------------


def test_seed_can_create_twelve_demo_complaints_in_every_state(seeded):
    mail.outbox.clear()
    call_command("seed", "--demo", "--complaints", verbosity=0)
    assert Complaint.objects.count() == 12
    statuses = set(Complaint.objects.values_list("status", flat=True))
    assert statuses >= {"submitted", "investigation", "pending_scar", "hod_review", "fm_review", "approved", "response_sent", "closed"}
    assert Complaint.objects.filter(deleted_at__isnull=False).count() == 1
    assert Complaint.objects.filter(hod_decision="invalid").count() == 1
    assert mail.outbox == [] and NotificationLog.objects.filter(success=True).exists()  # logged, not sent
    from django.contrib.auth import get_user_model

    counts = reporting.summary(reporting.scoped(get_user_model().objects.get(username="management")))
    assert counts["total"] == 12 and counts["valid_closed"] >= 2 and counts["deleted"] == 1
    assert Complaint.objects.overdue().exists()  # some are overdue, so the reminders have something to do


def test_the_demo_complaints_are_made_only_once(seeded):
    call_command("seed", "--demo", "--complaints", verbosity=0)
    first = set(Complaint.objects.values_list("reference", flat=True))
    call_command("seed", "--demo", "--complaints", verbosity=0)  # a redeploy
    assert set(Complaint.objects.values_list("reference", flat=True)) == first and Complaint.objects.count() == 12


def test_demo_complaints_need_the_demo_users(db):
    with pytest.raises(CommandError, match="--complaints needs --demo"):
        call_command("seed", "--complaints", verbosity=0)


def test_the_demo_complaints_carry_on_the_reference_numbers(seeded, as_user):
    call_command("seed", "--demo", "--complaints", verbosity=0)
    from tests.test_complaints import lodge

    lodge(as_user("sales"), submit="1")
    newest = Complaint.objects.latest("id")
    assert newest.reference == f"FA/{timezone.localdate().year}/0005"  # the demo has four Factory A complaints


# --- Sending the reminders from the page (a host with no scheduler) -----------------------------------------------------------


def test_the_coordinator_can_send_the_due_reminders_from_the_notifications_page(seeded, as_user):
    call_command("seed", "--demo", "--complaints", verbosity=0)
    client = as_user("mr")
    page = client.get(reverse("notifications")).content.decode()
    import re

    due = int(re.search(r"<strong>(\d+)</strong> reminders? (?:is|are) due now", page).group(1))
    assert due > 0 and "Send due reminders now" in page
    mail.outbox.clear()
    response = client.post(reverse("run_reminders"), follow=True)
    assert f"{due} reminder" in response.content.decode() and len(mail.outbox) == due
    assert NotificationLog.objects.filter(kind="reminder", success=True).count() == due
    again = client.post(reverse("run_reminders"), follow=True)
    assert "No reminders are due." in again.content.decode() and len(mail.outbox) == due


def test_only_the_coordinator_can_send_reminders(seeded, as_user):
    call_command("seed", "--demo", "--complaints", verbosity=0)
    mail.outbox.clear()
    for username in ("management", "fa.pic", "fa.hod", "sales"):
        client = as_user(username)
        assert client.post(reverse("run_reminders")).status_code == 302
    assert mail.outbox == [] and not NotificationLog.objects.filter(kind="reminder").exists()
    assert "Send due reminders now" not in as_user("management").get(reverse("notifications")).content.decode()
    assert as_user("mr").get(reverse("run_reminders")).status_code == 405  # a page view never sends anything


def test_an_administrator_can_be_created_from_the_environment(db, monkeypatch):
    """What build.sh does when DJANGO_SUPERUSER_* is set: there is no shell on a free plan."""
    from accounts.models import User

    monkeypatch.setenv("DJANGO_SUPERUSER_USERNAME", "siteadmin")
    monkeypatch.setenv("DJANGO_SUPERUSER_EMAIL", "admin@example.com")
    monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD", "a-long-demo-admin-password")
    call_command("createsuperuser", interactive=False, verbosity=0)
    admin = User.objects.get(username="siteadmin")
    assert admin.is_superuser and admin.is_staff and admin.check_password("a-long-demo-admin-password")
    with pytest.raises(CommandError, match="already taken"):  # a repeat deploy: build.sh carries on
        call_command("createsuperuser", interactive=False, verbosity=0)


# --- The files Render reads ---------------------------------------------------------------------------------------------------


def test_the_build_script_is_a_linux_script():
    script = (ROOT / "build.sh").read_bytes()
    assert script.startswith(b"#!/usr/bin/env bash") and b"\r" not in script  # Windows line endings would break it
    text = script.decode()
    for step in ("pip install -r requirements.txt", "collectstatic --no-input", "migrate --no-input", "seed --demo --complaints"):
        assert step in text
    assert "createsuperuser --noinput" in text
    assert "*.sh text eol=lf" in (ROOT / ".gitattributes").read_text()


def test_the_blueprint_names_what_render_needs():
    text = (ROOT / "render.yaml").read_text()
    for expected in (
        "plan: free", "runtime: python", "buildCommand: bash build.sh", "gunicorn config.wsgi:application",
        "healthCheckPath: /healthz/", "DJANGO_SECRET_KEY", "generateValue: true", "fromDatabase", "DJANGO_DEBUG",
    ):  # fmt: skip
        assert expected in text, expected
    assert text.count("plan: free") == 2  # the web service and the database


def test_every_package_the_host_installs_is_listed():
    needed = {"django", "gunicorn", "whitenoise", "psycopg", "dj-database-url", "reportlab", "openpyxl"}
    listed = {line.split(">")[0].split("[")[0].split("=")[0].strip().lower() for line in (ROOT / "requirements.txt").read_text().splitlines() if line.strip()}
    assert needed <= listed
