"""Step 4: the notification log and the reminders."""

import datetime
import io
import re

import pytest
from django.core import mail
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from complaints import reminders, workflow
from complaints.models import Complaint, ComplaintType, NotificationLog
from complaints.workflow import WorkflowError
from tests.test_complaints import as_user, complete_data, lodge  # noqa: F401  (fixtures and helpers)
from tests.test_workflow import (  # noqa: F401
    approved,
    assigned,
    at_fm,
    at_hod,
    fresh,
    issue,
    replied,
    submitted,
    write_findings,
)

S = Complaint.Status
DAY = datetime.timedelta(days=1)


def later(complaint, days):
    """A moment ``days`` days after the complaint reached its current stage."""
    return fresh(complaint).status_changed_at + days * DAY


def broken_mail(monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("mail server down")

    monkeypatch.setattr("complaints.emails.EmailMessage.send", broken)


# --- The notification log ----------------------------------------------------------------------------


def test_every_email_is_logged(as_user, user_of):
    mail.outbox.clear()
    complaint = replied(as_user, user_of)
    logs = NotificationLog.objects.filter(complaint=complaint)
    assert logs.filter(success=False).count() == 0
    assert logs.count() == len(mail.outbox)  # one line for every email that went out
    kinds = set(logs.values_list("kind", flat=True))
    assert {"submitted", "assigned", "findings", "hod_to_fm", "fm_decision", "customer_reply", "reply_sent"} <= kinds


def test_a_log_line_says_who_what_and_how(as_user, user_of):
    complaint = assigned(as_user, user_of, "fa.pic", "fa.labmgr")
    line = NotificationLog.objects.get(complaint=complaint, kind="assigned")
    assert set(line.to_addresses.split(", ")) == {"fa.pic@example.com", "fa.labmgr@example.com"}
    assert complaint.reference in line.subject and complaint.reference in line.body
    assert line.attachment_names.endswith("summary.pdf") and line.success and line.error == ""


def test_a_failed_email_is_logged_and_the_step_still_happens(as_user, user_of, monkeypatch):
    broken_mail(monkeypatch)
    response = as_user("sales").post(reverse("complaint_new"), {**complete_data(), "submit": "1"})
    assert response.status_code == 302
    complaint = Complaint.objects.get()
    assert complaint.status == S.SUBMITTED
    line = NotificationLog.objects.get(kind="submitted")
    assert not line.success and "mail server down" in line.error and line.to_addresses == "mr@example.com"


def test_a_failed_scar_is_logged_even_though_it_is_rolled_back(as_user, user_of, monkeypatch):
    complaint = assigned(as_user, user_of)
    broken_mail(monkeypatch)
    with pytest.raises(WorkflowError):
        issue(complaint, user_of("fa.pic"))
    assert fresh(complaint).status == S.INVESTIGATION
    line = NotificationLog.objects.get(kind="scar")
    assert not line.success and line.to_addresses == "quality@sealmakers.example" and "mail server down" in line.error


def test_a_failed_customer_reply_is_logged_even_though_it_is_rolled_back(as_user, user_of, monkeypatch):
    complaint = approved(as_user, user_of)
    broken_mail(monkeypatch)
    with pytest.raises(WorkflowError):
        workflow.send_reply(complaint, user_of("fa.pic"), "Subject", "Body")
    assert fresh(complaint).status == S.APPROVED
    line = NotificationLog.objects.get(kind="customer_reply")
    assert not line.success and line.to_addresses == "contact@example.com"


def test_an_email_with_nobody_to_send_to_is_logged_as_failed(as_user, user_of):
    from accounts.models import Role, User

    User.objects.filter(role=Role.COORDINATOR).update(is_active=False)
    lodge(as_user("sales"), submit="1")
    line = NotificationLog.objects.get(kind="submitted")
    assert not line.success and "No email address" in line.error and mail.outbox == []


def test_only_the_coordinator_and_management_can_open_the_log(as_user, user_of):
    complaint = assigned(as_user, user_of)
    for username in ("mr", "management"):
        response = as_user(username).get(reverse("notifications"))
        assert response.status_code == 200 and complaint.reference in response.content.decode()
    for username in ("fa.pic", "fa.hod", "fa.fm", "sales"):
        assert as_user(username).get(reverse("notifications")).status_code == 302
    assert as_user("fa.pic").get(reverse("home")).content.decode().count("Notifications</a>") == 0
    assert "Notifications</a>" in as_user("mr").get(reverse("home")).content.decode()


def test_the_log_can_be_filtered(as_user, user_of, monkeypatch):
    first = assigned(as_user, user_of)
    broken_mail(monkeypatch)
    second = submitted(as_user)  # its email to the Coordinator fails
    client = as_user("mr")

    def refs(**params):
        html = client.get(reverse("notifications"), params).content.decode()
        return re.findall(r"[A-Z]{2}/\d{4}/\d{4}", " ".join(re.findall(r'<td><a href="[^"]*">([^<]+)</a>', html)))

    assert set(refs()) == {first.reference, second.reference}
    assert set(refs(result="failed")) == {second.reference}
    assert set(refs(result="sent", kind="assigned")) == {first.reference}
    assert refs(q="fa.pic@example.com") == [first.reference]
    assert refs(kind="reminder") == []


def test_the_coordinator_sees_the_notifications_of_a_complaint(as_user, user_of):
    complaint = assigned(as_user, user_of)
    url = reverse("complaint_detail", args=[complaint.pk])
    assert "Assigned to PIC" in as_user("mr").get(url).content.decode()
    assert "Assigned to PIC" in as_user("management").get(url).content.decode()
    assert "Open the log" not in as_user("fa.pic").get(url).content.decode()


# --- Reminders -------------------------------------------------------------------------------------------


def recipients(mail_message):
    return set(mail_message.to)


def test_a_reminder_is_due_after_the_days_set_for_the_type(as_user, user_of):
    complaint = assigned(as_user, user_of)  # PIC investigation, Packaging type: 3 days
    mail.outbox.clear()
    assert reminders.send_due_reminders(later(complaint, 2.9)) == []
    assert mail.outbox == []
    results = reminders.send_due_reminders(later(complaint, 3))
    assert [r["complaint"] for r in results] == [complaint] and results[0]["sent"]
    (email,) = mail.outbox
    assert recipients(email) == {"fa.pic@example.com"}
    assert email.subject == f"Reminder: complaint {complaint.reference} has been waiting for 3 days"
    assert "reminder 1" in email.body and "waiting for PIC for 3 days" in email.body
    assert f"/complaints/{complaint.pk}/" in email.body
    line = NotificationLog.objects.get(kind="reminder")
    assert line.complaint == complaint and line.success


def test_each_stage_reminds_the_person_responsible(as_user, user_of):
    builders = {
        "mr": lambda: submitted(as_user),
        "fa.pic": lambda: assigned(as_user, user_of),
        "fa.hod": lambda: at_hod(as_user, user_of),
        "fa.fm": lambda: at_fm(as_user, user_of),
        "fa.pic (reply due)": lambda: approved(as_user, user_of),
        "mr (effectiveness)": lambda: replied(as_user, user_of),
    }
    expected = {"mr": "mr", "fa.pic": "fa.pic", "fa.hod": "fa.hod", "fa.fm": "fa.fm",
                "fa.pic (reply due)": "fa.pic", "mr (effectiveness)": "mr"}  # fmt: skip
    for label, build in builders.items():
        complaint = build()
        mail.outbox.clear()
        results = reminders.send_due_reminders(later(complaint, 4))
        mine = [r for r in results if r["complaint"] == complaint]
        assert len(mine) == 1, label
        sent_to = {m.to[0] for m in mail.outbox if complaint.reference in m.subject}
        assert sent_to == {f"{expected[label]}@example.com"}, label


def test_a_pending_scar_reminds_the_pic(as_user, user_of):
    complaint = assigned(as_user, user_of)
    issue(complaint, user_of("fa.pic"))
    mail.outbox.clear()
    reminders.send_due_reminders(later(complaint, 3))
    (email,) = mail.outbox
    assert recipients(email) == {"fa.pic@example.com"} and "SCAR" in email.body  # status shown in the email


def test_reminders_repeat_every_period_until_somebody_acts(as_user, user_of):
    complaint = assigned(as_user, user_of)
    mail.outbox.clear()
    start = fresh(complaint).status_changed_at
    assert len(reminders.send_due_reminders(start + 3 * DAY)) == 1
    assert fresh(complaint).reminder_count == 1 and fresh(complaint).last_reminder_at == start + 3 * DAY
    assert reminders.send_due_reminders(start + 4 * DAY) == []  # not again the next day
    assert reminders.send_due_reminders(start + 5 * DAY) == []
    second = reminders.send_due_reminders(start + 6 * DAY)
    assert len(second) == 1 and second[0]["number"] == 2 and second[0]["days"] == 6
    assert "reminder 2" in mail.outbox[-1].body and len(mail.outbox) == 2


def test_acting_starts_the_clock_again(as_user, user_of):
    complaint = assigned(as_user, user_of)
    start = fresh(complaint).status_changed_at
    reminders.send_due_reminders(start + 3 * DAY)
    write_findings(complaint, user_of("fa.pic"))
    workflow.submit_to_hod(fresh(complaint), user_of("fa.pic"))
    complaint = fresh(complaint)
    assert complaint.reminder_count == 0 and complaint.last_reminder_at is None
    mail.outbox.clear()
    assert reminders.send_due_reminders(complaint.status_changed_at + 2 * DAY) == []
    reminders.send_due_reminders(complaint.status_changed_at + 3 * DAY)
    assert recipients(mail.outbox[0]) == {"fa.hod@example.com"}  # now it waits for the HOD


def test_the_days_are_set_for_each_complaint_type(as_user, user_of):
    ComplaintType.objects.filter(name="Packaging").update(reminder_days=5)
    complaint = assigned(as_user, user_of)
    assert fresh(complaint).reminder_days == 5
    assert reminders.send_due_reminders(later(complaint, 4)) == []
    assert len(reminders.send_due_reminders(later(complaint, 5))) == 1


def test_drafts_closed_and_deleted_complaints_are_not_reminded(as_user, user_of):
    lodge(as_user("sales"), save="1")
    draft = Complaint.objects.get()
    closed = replied(as_user, user_of)
    workflow.save_verification(closed, user_of("mr"), datetime.date(2026, 12, 1), "ok", None, "")
    workflow.close_case(fresh(closed), user_of("mr"))
    deleted = assigned(as_user, user_of)
    workflow.delete(deleted, user_of("mr"), "Duplicate")
    far = timezone.now() + 90 * DAY
    assert [r["complaint"] for r in reminders.send_due_reminders(far)] == []
    assert not Complaint.objects.overdue(far).exists()
    assert draft.is_draft


def test_with_nobody_set_up_the_coordinator_is_told(as_user, user_of):
    from accounts.models import User

    complaint = at_hod(as_user, user_of)
    User.objects.filter(username="fa.hod").update(is_active=False)
    mail.outbox.clear()
    results = reminders.send_due_reminders(later(complaint, 3))
    assert results[0]["sent"] and recipients(mail.outbox[0]) == {"mr@example.com"}
    assert "Nobody is set up in the platform as HOD for Factory A" in mail.outbox[0].body


def test_a_deputy_is_reminded_while_the_hod_is_away(as_user, user_of):
    hod, deputy = user_of("fa.hod"), user_of("fb.hod")
    hod.deputy, hod.away_until = deputy, datetime.date.today() + datetime.timedelta(days=10)
    hod.save()
    complaint = at_hod(as_user, user_of)
    mail.outbox.clear()
    reminders.send_due_reminders(later(complaint, 3))
    assert recipients(mail.outbox[0]) == {"fb.hod@example.com"}


def test_a_reminder_that_cannot_be_sent_is_tried_again_next_time(as_user, user_of, monkeypatch):
    complaint = assigned(as_user, user_of)
    now = later(complaint, 3)
    with monkeypatch.context() as patch:
        broken_mail(patch)
        (result,) = reminders.send_due_reminders(now)
    assert not result["sent"]
    assert fresh(complaint).last_reminder_at is None and fresh(complaint).reminder_count == 0
    failed = NotificationLog.objects.get(kind="reminder", success=False)
    assert "mail server down" in failed.error
    (again,) = reminders.send_due_reminders(now + DAY)
    assert again["sent"] and again["number"] == 1
    assert NotificationLog.objects.filter(kind="reminder", success=True).count() == 1


def test_a_dry_run_sends_nothing_and_changes_nothing(as_user, user_of):
    complaint = assigned(as_user, user_of)
    mail.outbox.clear()
    (result,) = reminders.send_due_reminders(later(complaint, 3), dry_run=True)
    assert result["complaint"] == complaint and not result["sent"]
    assert mail.outbox == [] and fresh(complaint).reminder_count == 0
    assert not NotificationLog.objects.filter(kind="reminder").exists()


def test_the_command_lists_and_sends(as_user, user_of):
    complaint = assigned(as_user, user_of)
    Complaint.objects.filter(pk=complaint.pk).update(status_changed_at=timezone.now() - 4 * DAY)
    mail.outbox.clear()

    dry = io.StringIO()
    call_command("send_reminders", "--dry-run", stdout=dry)
    assert f"would remind Sample PIC FA about {complaint.reference} (4 days, reminder 1)" in dry.getvalue()
    assert "1 reminder(s) are due." in dry.getvalue() and mail.outbox == []

    out = io.StringIO()
    call_command("send_reminders", stdout=out)
    assert "reminded Sample PIC FA" in out.getvalue() and "1 reminder(s) sent." in out.getvalue()
    assert len(mail.outbox) == 1
    again = io.StringIO()
    call_command("send_reminders", stdout=again)
    assert "0 reminder(s) sent." in again.getvalue() and len(mail.outbox) == 1


def test_overdue_complaints_are_flagged(as_user, user_of):
    fresh_one = assigned(as_user, user_of)
    old = assigned(as_user, user_of)
    Complaint.objects.filter(pk=old.pk).update(status_changed_at=timezone.now() - 5 * DAY)
    old = fresh(old)
    assert old.is_overdue and old.days_waiting == 5 and not fresh(fresh_one).is_overdue
    assert list(Complaint.objects.overdue()) == [old]

    client = as_user("mr")
    html = client.get(reverse("home")).content.decode()
    assert re.findall(r'<div class="kpi[^"]*"><b>(\d+)</b>', html) == ["2", "0", "0", "1"]
    assert 'class="tag overdue"' in html
    only = client.get(reverse("home"), {"overdue": "on"}).content.decode()
    assert old.reference in only and fresh_one.reference not in only

    detail = as_user("fa.pic").get(reverse("complaint_detail", args=[old.pk])).content.decode()
    assert "overdue: 5 days at this stage" in detail and "5 days, overdue" in detail


def test_closed_and_draft_complaints_have_no_waiting_days(as_user, user_of):
    lodge(as_user("sales"), save="1")
    draft = Complaint.objects.get()
    Complaint.objects.filter(pk=draft.pk).update(status_changed_at=timezone.now() - 30 * DAY)
    assert fresh(draft).days_waiting == 0 and not fresh(draft).is_overdue
