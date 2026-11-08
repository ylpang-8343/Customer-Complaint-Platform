"""Emails sent by the platform.

Every email goes through ``_send``, which writes a line in the notification log. The ``notify_*``
functions return the number of emails sent (0 if nobody could be told) and raise ``DeliveryError``
if the mail server refuses. Call them through ``deliver`` where a failure must not stop the work:
it records the failure in the log and returns True or False.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.urls import reverse

from . import people
from .models import NotificationLog
from .pdf import complaint_summary_pdf, scar_pdf
from .summary import summary_sections

logger = logging.getLogger(__name__)
Kind = NotificationLog.Kind


class DeliveryError(Exception):
    """The mail server did not accept the email. ``entry`` holds what the log line should say."""

    def __init__(self, entry, original):
        self.entry = entry
        super().__init__(str(original))


def complaint_url(complaint):
    return settings.SITE_URL + reverse("complaint_detail", args=[complaint.pk])


def _addresses(users):
    seen, result = set(), []
    for user in users:
        if user is not None and user.email and user.email.lower() not in seen:
            seen.add(user.email.lower())
            result.append(user.email)
    return result


def record_failure(error):
    """Write the log line for an email that could not be sent (after any rollback has happened)."""
    NotificationLog.objects.create(success=False, **error.entry)


def _send(kind, complaint, subject, body, to, cc=(), attachments=(), reply_to=()):
    to = list(dict.fromkeys(to))
    cc = [address for address in dict.fromkeys(cc) if address not in to]
    entry = {
        "complaint": complaint,
        "kind": kind,
        "subject": subject,
        "to_addresses": ", ".join(to),
        "cc_addresses": ", ".join(cc),
        "body": body,
        "attachment_names": ", ".join(name for name, _, _ in attachments),
    }
    if not to:
        NotificationLog.objects.create(success=False, error="No email address to send it to.", **entry)
        return 0
    message = EmailMessage(
        subject=subject, body=body, from_email=settings.DEFAULT_FROM_EMAIL, to=to, cc=cc, reply_to=list(reply_to)
    )
    for name, content, mime in attachments:
        message.attach(name, content, mime)
    try:
        sent = message.send()
    except Exception as error:
        logger.exception("Could not send %s", subject)
        raise DeliveryError({**entry, "error": f"{type(error).__name__}: {error}"[:1000]}, error) from error
    NotificationLog.objects.create(success=True, **entry)
    return sent


def deliver(function, *args, **kwargs):
    """Run a ``notify_*`` function. A failure is logged and reported as False, never raised."""
    try:
        return bool(function(*args, **kwargs))
    except DeliveryError as error:
        record_failure(error)
    except Exception:
        logger.exception("Could not send %s", getattr(function, "__name__", function))
    return False


def _notice(
    kind, complaint, subject, intro, to, cc=(), comment="", comment_label="Comment", details="", attach_summary=False
):
    body = render_to_string(
        "email/notice.txt",
        {
            "complaint": complaint,
            "complaint_type": " / ".join(
                part for part in (str(complaint.complaint_type or ""), str(complaint.complaint_sub_type or "")) if part
            ),
            "intro": intro,
            "comment": comment,
            "comment_label": comment_label,
            "details": details,
            "url": complaint_url(complaint),
        },
    )
    attachments = []
    if attach_summary:
        attachments.append(
            (f"{complaint.reference.replace('/', '-')}-summary.pdf", complaint_summary_pdf(complaint), "application/pdf")
        )
    return _send(kind, complaint, subject, body, to, cc, attachments)


# Step 2
def notify_coordinator_of_submission(complaint):
    """When the form is submitted, the platform emails the Coordinator."""
    body = render_to_string(
        "email/complaint_submitted.txt",
        {"complaint": complaint, "sections": summary_sections(complaint), "url": complaint_url(complaint)},
    )
    subject = f"Customer complaint {complaint.reference} submitted for assignment"
    return _send(Kind.SUBMITTED, complaint, subject, body, _addresses(people.coordinators()))


# Step 3
def notify_pics_of_assignment(complaint, assigned_users, coordinator, remarks=""):
    return _notice(
        Kind.ASSIGNED,
        complaint,
        f"Customer complaint {complaint.reference} assigned to you for investigation",
        f"{coordinator} has assigned this customer complaint to you. A PDF summary is attached.",
        _addresses(assigned_users),
        comment=remarks,
        comment_label="Remarks from the Coordinator",
        attach_summary=True,
    )


# Step 4
def notify_hod_of_findings(complaint, pic):
    return _notice(
        Kind.FINDINGS,
        complaint,
        f"Complaint {complaint.reference}: investigation findings for your review",
        f"{pic} has submitted the investigation findings. Please decide whether the complaint is valid.",
        _addresses(people.hods(complaint)),
    )


# Step 4a
def send_scar(scar):
    complaint = scar.complaint
    pdf = (f"SCAR-{complaint.reference.replace('/', '-')}.pdf", scar_pdf(scar), "application/pdf")
    return _send(
        Kind.SCAR,
        complaint,
        scar.subject,
        scar.message,
        [scar.contact_email],
        cc=_addresses([scar.issued_by]),
        attachments=[pdf],
        reply_to=_addresses([scar.issued_by]),
    )


# Step 5
def notify_pics_of_hod_return(complaint, hod, comment):
    return _notice(
        Kind.HOD_RETURNED,
        complaint,
        f"Complaint {complaint.reference}: returned to you by the HOD",
        f"{hod} does not agree with the findings and has returned the complaint. Please improve the response and "
        "submit it to the HOD again.",
        _addresses(people.pics(complaint)),
        comment=comment,
        comment_label="HOD comment",
    )


def notify_fm_of_response(complaint, hod):
    decision = complaint.get_hod_decision_display()
    details = f"HOD decision: {decision}. Cause: {complaint.get_cause_display()}."
    if complaint.hod_reason:
        details += f"\nReason: {complaint.hod_reason}"
    return _notice(
        Kind.HOD_TO_FM,
        complaint,
        f"Complaint {complaint.reference}: response for your approval",
        f"{hod} has reviewed the response and submitted it for your approval.",
        _addresses(people.fms(complaint)),
        cc=_addresses(people.coordinators() + people.pics(complaint)),
        details=details,
    )


# Step 6
def notify_of_fm_decision(complaint, fm, approved, comment):
    if approved:
        subject = f"Complaint {complaint.reference}: approved by the FM"
        intro = f"{fm} has approved the response. The PIC can now reply to the customer."
    else:
        subject = f"Complaint {complaint.reference}: rejected by the FM"
        intro = (
            f"{fm} has rejected the response. The PIC should amend it (or ask the supplier to improve the SCAR) "
            "and submit it to the HOD again."
        )
    return _notice(
        Kind.FM_DECISION,
        complaint,
        subject,
        intro,
        _addresses(people.pics(complaint) + people.hods(complaint) + people.coordinators()),
        comment=comment,
        comment_label="FM comment",
    )


# Step 7
def send_customer_reply(complaint, reply, files, sender):
    return _send(
        Kind.CUSTOMER_REPLY,
        complaint,
        reply.subject,
        reply.body,
        [reply.to_email],
        cc=_addresses([sender]),
        attachments=list(files),
        reply_to=_addresses([sender]),
    )


def notify_of_reply_sent(complaint, sender):
    return _notice(
        Kind.REPLY_SENT,
        complaint,
        f"Complaint {complaint.reference}: reply sent to the customer",
        f"{sender} has sent the reply to the customer. A copy is saved in the complaint record.",
        _addresses(people.hods(complaint) + people.fms(complaint) + people.coordinators()),
    )


# Step 8
def notify_pics_not_effective(complaint, coordinator, comment):
    return _notice(
        Kind.NOT_EFFECTIVE,
        complaint,
        f"Complaint {complaint.reference}: corrective action not effective",
        f"{coordinator} found that the corrective action was not effective. The complaint is back with the PIC.",
        _addresses(people.pics(complaint)),
        cc=_addresses(people.hods(complaint)),
        comment=comment,
        comment_label="Coordinator comment",
    )


# Reminders
def send_reminder(complaint, recipients, days, number, note=""):
    """Remind the people a complaint is waiting for that it has been idle for ``days`` days."""
    role = people.responsible_role(complaint)
    plural = "" if days == 1 else "s"
    intro = (
        f"This is reminder {number}. Complaint {complaint.reference} has been waiting for {role} for "
        f"{days} day{plural} (reminders are set for every {complaint.reminder_days} days)."
    )
    return _notice(
        Kind.REMINDER,
        complaint,
        f"Reminder: complaint {complaint.reference} has been waiting for {days} day{plural}",
        intro,
        _addresses(recipients),
        details=note,
    )
