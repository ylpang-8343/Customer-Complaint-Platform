"""Reminders (proposal section 4): a complaint that has waited too long for an action nudges the person responsible.

``send_due_reminders`` is run once a day by ``manage.py send_reminders``. A complaint is due when its
current stage has lasted longer than the reminder days of its complaint type (3 unless the Coordinator
changed it in Settings). It is then reminded again every that-many days until somebody acts. Moving to
the next stage starts the clock again.
"""

from datetime import timedelta

from django.utils import timezone

from . import emails, people
from .models import Complaint


def _reference_time(complaint):
    return max(t for t in (complaint.status_changed_at, complaint.last_reminder_at) if t is not None)


def due_reminders(now=None):
    """The reminders that should go out now, as dicts. Nothing is sent or changed."""
    now = now or timezone.now()
    due = []
    complaints = Complaint.objects.overdue(now).select_related("factory", "complaint_type", "complaint_sub_type")
    for complaint in complaints.order_by("status_changed_at", "id"):
        if now - _reference_time(complaint) < timedelta(days=complaint.reminder_days):
            continue
        recipients, note = people.responsible_users(complaint), ""
        if not recipients:
            # Nobody is set up for this step (for example no HOD for the factory): tell the Coordinator.
            recipients = people.coordinators()
            note = (
                f"Nobody is set up in the platform as {people.responsible_role(complaint)} for "
                f"{complaint.factory}, so this reminder has come to you."
            )
        due.append(
            {
                "complaint": complaint,
                "recipients": recipients,
                "days": max(1, (now - complaint.status_changed_at).days),
                "number": complaint.reminder_count + 1,
                "note": note,
            }
        )
    return due


def send_due_reminders(now=None, dry_run=False):
    """Send the reminders that are due and return what was done, one dict per complaint.

    A reminder that could not be emailed is logged and tried again the next time this runs.
    """
    now = now or timezone.now()
    results = []
    for item in due_reminders(now):
        complaint = item["complaint"]
        sent = False
        if not dry_run:
            sent = emails.deliver(
                emails.send_reminder, complaint, item["recipients"], item["days"], item["number"], item["note"]
            )
            if sent:
                # An update, not a save: the reminder clock is bookkeeping, not a change anyone made.
                Complaint.objects.filter(pk=complaint.pk).update(
                    last_reminder_at=now, reminder_count=complaint.reminder_count + 1
                )
        results.append({**item, "sent": sent})
    return results
