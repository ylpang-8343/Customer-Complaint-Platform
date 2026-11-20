"""Demo feedback: the system sends the reminders by itself, not only when the Coordinator presses the button."""

import datetime
import json

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from complaints import scheduler
from complaints.models import Complaint, JobRun, NotificationLog
from tests.test_complaints import as_user  # noqa: F401  (fixture)
from tests.test_workflow import assigned, fresh  # noqa: F401

HOUR = datetime.timedelta(hours=1)


@pytest.fixture
def overdue(as_user, user_of):
    """A complaint waiting for its PIC for five days (the reminder is due after three)."""
    complaint = assigned(as_user, user_of, "fa.pic")
    Complaint.objects.filter(pk=complaint.pk).update(status_changed_at=timezone.now() - 5 * datetime.timedelta(days=1))
    mail.outbox.clear()
    return fresh(complaint)


def reminders_sent():
    return NotificationLog.objects.filter(kind="reminder").count()


def test_the_check_sends_the_due_reminders_by_itself(overdue):
    results = scheduler.run_if_due()
    assert results and all(item["sent"] for item in results)
    assert reminders_sent() == 1 and mail.outbox[0].to == ["fa.pic@example.com"]
    assert JobRun.objects.get(name="reminders").last_result == "1 sent"


def test_a_second_check_in_the_same_interval_sends_nothing_more(overdue):
    scheduler.run_if_due()
    assert scheduler.run_if_due() is None  # the slot is taken, as it is when two workers look at once
    assert reminders_sent() == 1


def test_the_slot_opens_again_after_the_interval(overdue):
    scheduler.run_if_due()
    later = timezone.now() + 2 * HOUR
    assert scheduler.claim(now=later) is True
    assert scheduler.claim(now=later) is False


def test_nothing_due_is_recorded_as_such(as_user, user_of):
    assigned(as_user, user_of, "fa.pic")
    scheduler.run_if_due()
    assert JobRun.objects.get(name="reminders").last_result == "none due" and reminders_sent() == 0


def test_a_request_starts_the_check_when_it_is_switched_on(overdue, client, settings, monkeypatch):
    settings.AUTO_REMINDERS = True
    monkeypatch.setattr(scheduler, "_last_look", 0.0)
    started = []
    monkeypatch.setattr(scheduler, "_spawn", lambda function: (started.append(1), function()))
    client.get("/healthz/")
    assert started == [1] and reminders_sent() == 1
    client.get("/healthz/")  # this process looked a moment ago: it does not look again
    assert started == [1]


def test_nothing_starts_when_it_is_switched_off(overdue, client, settings, monkeypatch):
    settings.AUTO_REMINDERS = False
    monkeypatch.setattr(scheduler, "_last_look", 0.0)
    monkeypatch.setattr(scheduler, "_spawn", lambda function: pytest.fail("the check must not start"))
    client.get("/healthz/")
    assert reminders_sent() == 0


def test_a_failing_check_never_breaks_the_page(overdue, client, settings, monkeypatch):
    settings.AUTO_REMINDERS = True
    monkeypatch.setattr(scheduler, "_last_look", 0.0)
    monkeypatch.setattr(scheduler, "_spawn", lambda function: function())

    def broken(*args, **kwargs):
        raise RuntimeError("database trouble")

    monkeypatch.setattr(scheduler, "run_if_due", broken)
    assert client.get("/healthz/").status_code == 200


# --- The address for an outside scheduler -------------------------------------------------------------------------------------


def test_the_task_address_does_not_exist_without_a_token(overdue, client, settings):
    settings.REMINDER_TOKEN = ""
    assert client.get(reverse("reminders_task"), {"token": ""}).status_code == 404
    assert reminders_sent() == 0


def test_a_wrong_token_is_refused_and_the_right_one_sends(overdue, client, settings):
    settings.REMINDER_TOKEN = "secret-123"
    assert client.get(reverse("reminders_task"), {"token": "wrong"}).status_code == 404
    assert client.get(reverse("reminders_task")).status_code == 404
    assert reminders_sent() == 0
    response = client.get(reverse("reminders_task"), {"token": "secret-123"})
    assert response.status_code == 200 and json.loads(response.content) == {"ran": True, "due": 1, "sent": 1, "failed": 0}
    again = client.get(reverse("reminders_task"), HTTP_AUTHORIZATION="Bearer secret-123")
    assert json.loads(again.content)["ran"] is False and reminders_sent() == 1  # not more often than every 10 minutes


def test_the_notifications_page_says_how_reminders_are_sent(overdue, as_user, settings, monkeypatch):
    monkeypatch.setattr(scheduler, "_spawn", lambda function: None)  # the page itself must not start a real thread here
    settings.AUTO_REMINDERS = True
    settings.REMINDER_TOKEN = "secret-123"
    scheduler.run_if_due()
    html = as_user("mr").get(reverse("notifications")).content.decode()
    assert "Reminders are sent automatically" in html and "every 60 minutes" in html and "1 sent" in html
    assert "/tasks/reminders/" in html and "secret-123" not in html  # the token itself is never shown
    settings.AUTO_REMINDERS = False
    assert "switched off" in as_user("mr").get(reverse("notifications")).content.decode()
