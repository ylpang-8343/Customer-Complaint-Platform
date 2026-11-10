"""Does the email actually leave the site? Choosing a mail service, saying so when none is set up, and the test email."""

import json

import pytest
import requests
from django.core import mail
from django.test import override_settings
from django.urls import reverse

from complaints import emails
from complaints.models import NotificationLog
from tests.test_complaints import as_user, lodge  # noqa: F401  (fixtures and helpers)
from tests.test_deploy import read_settings

CONSOLE = "django.core.mail.backends.console.EmailBackend"


# --- Which mail service the settings choose ---------------------------------------------------------------------------


def test_with_nothing_set_emails_are_only_written_to_the_log():
    assert read_settings()["EMAIL_BACKEND"] == CONSOLE


@pytest.mark.parametrize(
    "variable, backend",
    [
        ("BREVO_API_KEY", "anymail.backends.brevo.EmailBackend"),
        ("RESEND_API_KEY", "anymail.backends.resend.EmailBackend"),
        ("SENDGRID_API_KEY", "anymail.backends.sendgrid.EmailBackend"),
        ("POSTMARK_SERVER_TOKEN", "anymail.backends.postmark.EmailBackend"),
    ],
)
def test_an_api_key_selects_that_mail_service(variable, backend):
    s = read_settings(**{variable: "secret-key"})
    assert s["EMAIL_BACKEND"] == backend and s["ANYMAIL"][variable] == "secret-key"


def test_mailgun_needs_its_key_and_its_domain():
    s = read_settings(MAILGUN_API_KEY="key", MAILGUN_SENDER_DOMAIN="mg.example.com")
    assert s["EMAIL_BACKEND"] == "anymail.backends.mailgun.EmailBackend"
    assert s["ANYMAIL"] == {"MAILGUN_API_KEY": "key", "MAILGUN_SENDER_DOMAIN": "mg.example.com"}


def test_a_mail_service_is_preferred_to_smtp_and_an_explicit_backend_to_both():
    both = read_settings(BREVO_API_KEY="k", EMAIL_HOST="smtp.example.com")
    assert both["EMAIL_BACKEND"] == "anymail.backends.brevo.EmailBackend"
    smtp = read_settings(EMAIL_HOST="smtp.example.com")
    assert smtp["EMAIL_BACKEND"] == "django.core.mail.backends.smtp.EmailBackend"
    explicit = read_settings(BREVO_API_KEY="k", EMAIL_HOST="smtp.example.com", EMAIL_BACKEND="django.core.mail.backends.filebased.EmailBackend")
    assert explicit["EMAIL_BACKEND"] == "django.core.mail.backends.filebased.EmailBackend"


def test_the_sender_address_and_the_company_name_come_from_the_environment():
    s = read_settings(DEFAULT_FROM_EMAIL="complaints@example.org", COMPANY_NAME="Example Oils Sdn Bhd")
    assert s["DEFAULT_FROM_EMAIL"] == "complaints@example.org" and s["COMPANY_NAME"] == "Example Oils Sdn Bhd"
    assert read_settings()["COMPANY_NAME"] == "BEST Group"


# --- Saying so when nothing is delivered ----------------------------------------------------------------------------------


def test_an_email_is_marked_not_delivered_when_no_mail_server_is_set_up(as_user):
    with override_settings(EMAIL_BACKEND=CONSOLE):
        assert not emails.delivers_email()
        lodge(as_user("sales"), submit="1")
    line = NotificationLog.objects.get(kind="submitted")
    assert line.success and not line.delivered  # the step worked; the email never left the server


def test_an_email_is_marked_delivered_when_a_mail_server_takes_it(as_user):
    assert emails.delivers_email()  # the tests use the in-memory outbox, which stands in for a real server
    lodge(as_user("sales"), submit="1")
    assert NotificationLog.objects.get(kind="submitted").delivered


def test_the_notifications_page_warns_that_nothing_is_delivered(as_user):
    client = as_user("mr")
    lodge(as_user("sales"), submit="1")
    with override_settings(EMAIL_BACKEND=CONSOLE):
        html = client.get(reverse("notifications")).content.decode()
    assert "No mail server is set up on this site" in html
    assert "No mail server is set up on this site" not in client.get(reverse("notifications")).content.decode()


def test_a_log_line_says_not_delivered_instead_of_sent(as_user):
    client = as_user("mr")
    with override_settings(EMAIL_BACKEND=CONSOLE):
        lodge(as_user("sales"), submit="1")
        html = client.get(reverse("notifications")).content.decode()
    assert "Not delivered" in html and 'status-response_sent">Sent<' not in html
    delivered = client.get(reverse("notifications")).content.decode()
    assert "Not delivered" in delivered  # the line itself is still marked, whatever the setting is now


def test_the_log_can_be_filtered_by_not_delivered(as_user):
    client = as_user("mr")
    with override_settings(EMAIL_BACKEND=CONSOLE):
        lodge(as_user("sales"), submit="1")
    lodge(as_user("sales"), submit="1")
    def rows(result):
        html = client.get(reverse("notifications"), {"result": result}).content.decode()
        return html.split("<tbody>")[1].split("</tbody>")[0]  # the table, not the drop-down above it

    only = rows("notdelivered")
    assert only.count("Complaint submitted") == 1 and "Not delivered" in only
    sent = rows("sent")
    assert sent.count("Complaint submitted") == 1 and "Not delivered" not in sent


# --- The test email ----------------------------------------------------------------------------------------------------------


def test_the_coordinator_sends_a_test_email(as_user):
    client = as_user("mr")
    mail.outbox.clear()
    response = client.post(reverse("send_test_email"), {"address": "mum@gmail.example"}, follow=True)
    assert "A test email was sent to mum@gmail.example" in response.content.decode()
    (message,) = mail.outbox
    assert message.to == ["mum@gmail.example"] and message.subject == "Test email from the Customer Complaint Platform"
    assert "Sample Coordinator" in message.body and message.reply_to == ["mr@example.com"]
    line = NotificationLog.objects.get(kind="test")
    assert line.complaint is None and line.success and line.delivered and line.to_addresses == "mum@gmail.example"


def test_the_test_email_says_so_when_nothing_can_be_delivered(as_user):
    client = as_user("mr")
    with override_settings(EMAIL_BACKEND=CONSOLE):
        response = client.post(reverse("send_test_email"), {"address": "mum@gmail.example"}, follow=True)
    html = response.content.decode()
    assert "nothing was delivered" in html and "A test email was sent" not in html
    assert not NotificationLog.objects.get(kind="test").delivered


def test_the_test_email_reports_why_the_mail_server_refused_it(as_user, monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("Network is unreachable")

    monkeypatch.setattr("complaints.emails.EmailMessage.send", refuse)
    response = as_user("mr").post(reverse("send_test_email"), {"address": "mum@gmail.example"}, follow=True)
    assert "The test email could not be sent: OSError: Network is unreachable" in response.content.decode()
    line = NotificationLog.objects.get(kind="test")
    assert not line.success and "Network is unreachable" in line.error


def test_the_test_email_needs_a_real_address_and_the_coordinator(as_user):
    client = as_user("mr")
    mail.outbox.clear()
    for address in ("", "not an address"):
        assert "Type the email address" in client.post(reverse("send_test_email"), {"address": address}, follow=True).content.decode()
    for username in ("management", "fa.pic", "sales"):
        assert as_user(username).post(reverse("send_test_email"), {"address": "x@example.com"}).status_code == 302
    assert as_user("mr").get(reverse("send_test_email")).status_code == 405
    assert mail.outbox == [] and not NotificationLog.objects.filter(kind="test").exists()
    assert "Send test email" not in as_user("management").get(reverse("notifications")).content.decode()
    assert "Send test email" in client.get(reverse("notifications")).content.decode()


# --- A mail service over HTTPS (Brevo), with the network call replaced ------------------------------------------------------------


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code, self.reason = status, "OK" if status < 400 else "Error"
        self.text = json.dumps(payload)
        self.content = self.text.encode()
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} {self.reason}", response=self)


BREVO = dict(
    EMAIL_BACKEND="anymail.backends.brevo.EmailBackend", ANYMAIL={"BREVO_API_KEY": "test-brevo-key"},
    DEFAULT_FROM_EMAIL="complaints@example.org",
)  # fmt: skip


def test_an_email_goes_to_brevo_over_https_not_smtp(as_user, user_of, monkeypatch):
    sent = []

    def fake_request(self, method, url, **kwargs):
        sent.append((method, url, kwargs))
        return FakeResponse(201, {"messageId": "<202611100000.1@smtp-relay.mailin.fr>"})

    monkeypatch.setattr(requests.Session, "request", fake_request)
    with override_settings(**BREVO):
        assert emails.delivers_email()
        emails.send_test_email("mum@gmail.example", user_of("mr"))
    (method, url, kwargs), = sent
    assert method == "POST" and url.startswith("https://api.brevo.com/")  # port 443: not blocked on a free host
    assert kwargs["headers"]["api-key"] == "test-brevo-key"
    payload = json.loads(kwargs["data"])
    assert payload["sender"]["email"] == "complaints@example.org"
    assert payload["to"] == [{"email": "mum@gmail.example"}]
    assert payload["subject"] == "Test email from the Customer Complaint Platform" and "textContent" in payload
    line = NotificationLog.objects.get(kind="test")
    assert line.success and line.delivered


def test_a_refusal_from_the_mail_service_is_logged_with_its_reason(as_user, user_of, monkeypatch):
    def fake_request(self, method, url, **kwargs):
        return FakeResponse(400, {"code": "invalid_parameter", "message": "Sender is not valid or not verified"})

    monkeypatch.setattr(requests.Session, "request", fake_request)
    with override_settings(**BREVO):
        response = as_user("mr").post(reverse("send_test_email"), {"address": "mum@gmail.example"}, follow=True)
    assert "could not be sent" in response.content.decode()
    line = NotificationLog.objects.get(kind="test")
    assert not line.success and "not verified" in line.error


def test_a_workflow_step_still_works_when_the_mail_service_refuses(as_user, monkeypatch):
    monkeypatch.setattr(requests.Session, "request", lambda self, method, url, **kw: FakeResponse(401, {"message": "Key not found"}))
    with override_settings(**BREVO):
        response = lodge(as_user("sales"), submit="1")
    assert response.status_code == 302
    line = NotificationLog.objects.get(kind="submitted")
    assert not line.success and "Key not found" in line.error  # logged as failed; the complaint was still submitted
