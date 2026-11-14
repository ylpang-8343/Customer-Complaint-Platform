"""The complaint process after it is submitted (proposal section 4, steps 3 to 8).

Every step is a function that checks who may do it and in which status, changes the complaint,
writes a line in the complaint's history and sends the emails. The ``can_*`` functions say
whether a user may do a step now; the screens use them to show or hide buttons, and each step
checks again, so the rules hold even if someone posts to a URL directly.

Functions that notify people return True when every email went out. A failed email never undoes a
step, except where the email *is* the step (the SCAR and the reply to the customer).
"""

import logging
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import FactoryPIC

from . import emails, people
from .models import (
    Assignment,
    Attachment,
    Complaint,
    ComplaintEvent,
    CustomerReply,
    Investigation,
    LabResult,
    LabSection,
    RoutingRule,
    Scar,
    Verification,
)

logger = logging.getLogger(__name__)

STATUS = Complaint.Status
Action = ComplaintEvent.Action

ASSIGNABLE = {STATUS.SUBMITTED, STATUS.INVESTIGATION, STATUS.PENDING_SCAR}
INVESTIGATING = {STATUS.INVESTIGATION, STATUS.PENDING_SCAR}


class WorkflowError(Exception):
    """A step cannot be done. ``messages`` are written for the person who tried."""

    def __init__(self, messages):
        self.messages = [messages] if isinstance(messages, str) else list(messages)
        super().__init__(" ".join(self.messages))


# --- Who may do what ----------------------------------------------------------------


def _live(complaint):
    return not complaint.is_draft and not complaint.is_deleted


def can_assign(user, complaint):
    return _live(complaint) and complaint.status in ASSIGNABLE and people.is_coordinator(user)


def can_delete(user, complaint):
    return _live(complaint) and people.is_coordinator(user)


def can_investigate(user, complaint):
    """Enter or change the findings (also while a SCAR is pending)."""
    return _live(complaint) and complaint.status in INVESTIGATING and people.is_assigned(user, complaint)


def can_submit_to_hod(user, complaint):
    """Allowed while a SCAR is pending too, so the PIC is told why the submission is locked."""
    return can_investigate(user, complaint)


def can_edit_lab(user, complaint):
    return (
        _live(complaint)
        and complaint.sample_sent is True
        and complaint.status in INVESTIGATING
        and people.is_lab_staff(user, complaint)
    )


def can_issue_scar(user, complaint):
    return can_investigate(user, complaint) and complaint.status == STATUS.INVESTIGATION


def can_return_scar(user, complaint):
    return (
        _live(complaint)
        and complaint.status == STATUS.PENDING_SCAR
        and people.is_assigned(user, complaint)
    )


def can_review_as_hod(user, complaint):
    return _live(complaint) and complaint.status == STATUS.HOD_REVIEW and people.can_act_as_hod(user, complaint)


def can_review_as_fm(user, complaint):
    return _live(complaint) and complaint.status == STATUS.FM_REVIEW and people.can_act_as_fm(user, complaint)


def can_reply(user, complaint):
    return _live(complaint) and complaint.status == STATUS.APPROVED and people.is_assigned(user, complaint)


def can_verify(user, complaint):
    return _live(complaint) and complaint.status == STATUS.RESPONSE_SENT and people.is_coordinator(user)


def can_remove_attachment(user, attachment):
    """Files can be removed by whoever added them while their section is still open."""
    complaint = attachment.complaint
    if attachment.section == Attachment.Section.LODGING:
        return complaint.is_draft and complaint.lodged_by_id == user.pk
    if attachment.uploaded_by_id != user.pk or not _live(complaint):
        return False
    if attachment.section == Attachment.Section.COORDINATOR:
        return complaint.status in ASSIGNABLE
    if attachment.section in (Attachment.Section.INVESTIGATION, Attachment.Section.LAB):
        return complaint.status in INVESTIGATING
    return False


def available_actions(user, complaint):
    """The steps this user can take now, as (label, url name, is the main action)."""
    actions = []

    def add(allowed, label, url_name, main=True):
        if allowed:
            actions.append({"label": label, "url_name": url_name, "main": main})

    add(can_assign(user, complaint), "Assign PICs" if complaint.status == STATUS.SUBMITTED else "Change PICs", "complaint_assign", complaint.status == STATUS.SUBMITTED)
    add(can_investigate(user, complaint), "Investigation", "complaint_investigation", complaint.status == STATUS.INVESTIGATION)
    add(can_edit_lab(user, complaint), "Laboratory section", "complaint_lab", True)
    add(can_issue_scar(user, complaint), "Issue SCAR", "complaint_scar", False)
    add(can_return_scar(user, complaint), "Upload returned SCAR", "complaint_scar_return", True)
    add(can_review_as_hod(user, complaint), "HOD review", "complaint_hod", True)
    add(can_review_as_fm(user, complaint), "FM approval", "complaint_fm", True)
    add(can_reply(user, complaint), "Reply to customer", "complaint_reply", True)
    add(can_verify(user, complaint), "Effectiveness check and closing", "complaint_verify", True)
    add(can_delete(user, complaint), "Delete", "complaint_delete", False)
    return actions


def waiting_for_me(user):
    """A Q matching the complaints that wait for this user to act."""
    parts = []
    if people.is_coordinator(user):
        parts.append(Q(status__in=[STATUS.SUBMITTED, STATUS.RESPONSE_SENT]))
    parts.append(
        Q(
            pk__in=people.assigned_complaint_ids(user),
            status__in=[STATUS.INVESTIGATION, STATUS.PENDING_SCAR, STATUS.APPROVED],
        )
    )
    if hod_ids := people.hod_factories(user):
        # A HOD reviews the complaints of their department only, so each one is checked.
        reviewing = Complaint.objects.filter(factory_id__in=hod_ids, status=STATUS.HOD_REVIEW, deleted_at__isnull=True)
        parts.append(Q(pk__in=[c.pk for c in reviewing if people.can_act_as_hod(user, c)]))
    if fm_ids := people.fm_factories(user):
        parts.append(Q(factory_id__in=fm_ids, status=STATUS.FM_REVIEW))
    query = parts[0]
    for part in parts[1:]:
        query |= part
    return query & Q(deleted_at__isnull=True)


# --- Shared helpers -------------------------------------------------------------------


def _lock(complaint):
    """Reload the complaint with a row lock, so two people cannot act on it at the same moment."""
    complaint.refresh_from_db(from_queryset=Complaint.objects.select_for_update())
    for name in ("investigation", "lab", "verification", "reply"):  # forget related records read earlier
        complaint._state.fields_cache.pop(name, None)


def _move(complaint, actor, action, to_status=None, comment=""):
    previous = complaint.status
    if to_status and to_status != previous:
        complaint.status = to_status
        complaint.status_changed_at = timezone.now()
        complaint.last_reminder_at = None  # the reminder clock starts again at each stage
        complaint.reminder_count = 0
    complaint.save()
    ComplaintEvent.objects.create(
        complaint=complaint,
        action=action,
        actor=actor,
        comment=comment,
        from_status=previous,
        to_status=complaint.status,
    )


def _notify(function, *args, **kwargs):
    """Send a notification. A failure is written to the notification log and reported as False."""
    return emails.deliver(function, *args, **kwargs)


def store_files(complaint, files, user, section):
    for file in files:
        Attachment.objects.create(
            complaint=complaint,
            section=section,
            file=file,
            original_name=Path(file.name).name[:255],
            size=file.size,
            uploaded_by=user,
        )


def _require(allowed, message="You cannot do this now."):
    if not allowed:
        raise WorkflowError(message)


# --- Step 3: the Coordinator assigns the PICs ---------------------------------------------


def assign(complaint, actor, pic_entries, remarks="", files=(), department=None):
    """Choose the PICs. Only PICs who are new to the complaint are emailed.

    The department (which HOD reviews) is the one chosen, else the routing table's, else the first PIC's.
    """
    _require(can_assign(actor, complaint), "Only the Coordinator can assign a complaint that has been submitted.")
    pic_entries = list(pic_entries)
    if not pic_entries:
        raise WorkflowError("Choose at least one PIC.")
    if any(entry.factory_id != complaint.factory_id for entry in pic_entries):
        raise WorkflowError("A PIC must belong to the factory of the complaint.")

    with transaction.atomic():
        _lock(complaint)
        _require(can_assign(actor, complaint), "The complaint has moved on and can no longer be assigned.")
        first_time = complaint.status == STATUS.SUBMITTED
        existing = {a.user_id for a in complaint.assignments.all()}
        wanted = {entry.user_id for entry in pic_entries}
        complaint.assignments.exclude(user_id__in=wanted).delete()
        new_users = []
        for entry in pic_entries:
            if entry.user_id in existing:
                complaint.assignments.filter(user_id=entry.user_id).update(designation=entry.designation)
            else:
                Assignment.objects.create(complaint=complaint, user=entry.user, designation=entry.designation)
                new_users.append(entry.user)
        if first_time:
            complaint.received_by = actor
            complaint.acknowledged_at = timezone.now()
        complaint.coordinator_remarks = remarks
        complaint.department = (
            department
            or RoutingRule.department_for(complaint)
            or next((entry.department for entry in pic_entries if entry.department_id), None)
            or complaint.department
        )
        store_files(complaint, files, actor, Attachment.Section.COORDINATOR)
        names = ", ".join(f"{entry.user} ({entry.designation})" for entry in pic_entries)
        _move(complaint, actor, Action.ASSIGNED, STATUS.INVESTIGATION if first_time else None, comment=names)

    if not new_users:
        return True
    return _notify(emails.notify_pics_of_assignment, complaint, new_users, actor, remarks)


def auto_assign(complaint):
    """On submission, send the complaint to the department the routing table names (proposal feedback: auto assign).

    The PICs of that department at the factory are assigned and emailed, with the Coordinator copied. Returns
    (department, the users assigned, whether they were all emailed), or None when the complaint stays with the
    Coordinator: auto assign is off, no rule covers it (for example Others), or the department has no PIC at the factory.
    """
    if not getattr(settings, "AUTO_ASSIGN", True) or complaint.status != STATUS.SUBMITTED:
        return None
    department = RoutingRule.department_for(complaint)
    if department is None:
        return None
    entries = list(
        FactoryPIC.objects.filter(factory_id=complaint.factory_id, department=department, is_active=True, user__is_active=True)
        .select_related("user")
    )
    if not entries:
        return None
    with transaction.atomic():
        _lock(complaint)
        if complaint.status != STATUS.SUBMITTED:  # someone else got there first
            return None
        for entry in entries:
            Assignment.objects.create(complaint=complaint, user=entry.user, designation=entry.designation)
        complaint.department = department
        complaint.acknowledged_at = timezone.now()
        names = ", ".join(f"{entry.user} ({entry.designation})" for entry in entries)
        _move(complaint, None, Action.ASSIGNED, STATUS.INVESTIGATION, comment=f"Assigned automatically to {department}: {names}")
    users = [entry.user for entry in entries]
    notified = _notify(emails.notify_pics_of_assignment, complaint, users, None, "")
    return department, users, notified


def delete(complaint, actor, reason):
    """A complaint is never removed. It is marked Deleted with a reason and stays in the log."""
    _require(can_delete(actor, complaint), "Only the Coordinator can delete a complaint that has been submitted.")
    if not reason.strip():
        raise WorkflowError("Give the reason for deleting the complaint.")
    with transaction.atomic():
        _lock(complaint)
        _require(can_delete(actor, complaint), "This complaint is already deleted.")
        complaint.deleted_at = timezone.now()
        complaint.deleted_by = actor
        complaint.deleted_reason = reason.strip()
        _move(complaint, actor, Action.DELETED, comment=reason.strip())


# --- Step 4: the PIC investigates ----------------------------------------------------------


def save_investigation(
    complaint, actor, root_cause, correction, corrective_action, cause, files=(), investigation_summary=None, conclusion=None
):
    _require(can_investigate(actor, complaint), "You are not assigned to this complaint, or it is not under investigation.")
    with transaction.atomic():
        _lock(complaint)
        _require(can_investigate(actor, complaint), "The complaint has moved on and can no longer be changed.")
        investigation, _ = Investigation.objects.get_or_create(complaint=complaint)
        investigation.root_cause = root_cause
        investigation.correction = correction
        investigation.corrective_action = corrective_action
        if investigation_summary is not None:
            investigation.investigation_summary = investigation_summary
        if conclusion is not None:
            investigation.conclusion = conclusion
        investigation.save()
        complaint.cause = cause
        complaint.save()
        store_files(complaint, files, actor, Attachment.Section.INVESTIGATION)


def investigation_problems(complaint):
    """What still stops the PIC from submitting the findings to the HOD."""
    problems = []
    investigation = getattr(complaint, "investigation", None)
    for field, label in (
        ("root_cause", "the root cause analysis"),
        ("correction", "the correction"),
        ("corrective_action", "the corrective and preventive action"),
    ):
        if not investigation or not getattr(investigation, field).strip():
            problems.append(f"Enter {label}.")
    if not complaint.cause:
        problems.append("Say whether the cause is internal or external.")
    if complaint.sample_sent:
        lab = getattr(complaint, "lab", None)
        if lab is None or lab.acknowledged_on is None:
            problems.append("The laboratory section needs the lab acknowledgement date (a sample was sent).")
        elif not any(r.buyer_sample.strip() or r.retained_sample.strip() for r in lab.results.all()):
            problems.append("The laboratory section needs at least one test result (a sample was sent).")
    scar = complaint.scars.last()
    if complaint.status == STATUS.PENDING_SCAR or (scar and not scar.is_returned):
        problems.append("Submit to HOD is locked until the returned SCAR is uploaded.")
    return problems


def submit_to_hod(complaint, actor):
    _require(can_submit_to_hod(actor, complaint), "You cannot submit this complaint to the HOD now.")
    with transaction.atomic():
        _lock(complaint)
        _require(can_submit_to_hod(actor, complaint), "The complaint has moved on.")
        problems = investigation_problems(complaint)
        if problems:
            raise WorkflowError(problems)
        investigation = complaint.investigation
        investigation.respondent = actor
        investigation.submitted_at = timezone.now()
        investigation.save()
        _move(complaint, actor, Action.SENT_TO_HOD, STATUS.HOD_REVIEW)
    return _notify(emails.notify_hod_of_findings, complaint, actor)


def save_lab(complaint, actor, acknowledged_on, remarks, results, files=()):
    """``results`` maps a LabTest to (buyer sample result, retained sample result)."""
    _require(can_edit_lab(actor, complaint), "Only the laboratory staff assigned to this complaint can fill in this section.")
    with transaction.atomic():
        _lock(complaint)
        _require(can_edit_lab(actor, complaint), "The complaint has moved on and the laboratory section is closed.")
        lab, _ = LabSection.objects.get_or_create(complaint=complaint)
        lab.tested_by = actor
        lab.acknowledged_on = acknowledged_on
        lab.remarks = remarks
        lab.save()
        for test, (buyer_sample, retained_sample) in results.items():
            LabResult.objects.update_or_create(
                lab=lab, test=test, defaults={"buyer_sample": buyer_sample, "retained_sample": retained_sample}
            )
        store_files(complaint, files, actor, Attachment.Section.LAB)


# --- Step 4a: SCAR to an external party ----------------------------------------------------------


def issue_scar(complaint, actor, *args, **kwargs):
    """Send the SCAR. If the email cannot be sent, nothing changes except a line in the notification log."""
    try:
        return _issue_scar(complaint, actor, *args, **kwargs)
    except emails.DeliveryError as error:
        emails.record_failure(error)  # written after the rollback, so the failure is kept
        raise WorkflowError("The SCAR email could not be sent, so the SCAR was not issued. Try again later.")


def _issue_scar(complaint, actor, party_type, party_name, contact_email, subject, message):
    _require(can_issue_scar(actor, complaint), "You cannot issue a SCAR for this complaint now.")
    with transaction.atomic():
        _lock(complaint)
        _require(can_issue_scar(actor, complaint), "The complaint has moved on.")
        scar = Scar.objects.create(
            complaint=complaint,
            party_type=party_type,
            party_name=party_name,
            contact_email=contact_email,
            subject=subject,
            message=message,
            issued_by=actor,
        )
        try:
            emails.send_scar(scar)
        except emails.DeliveryError:
            raise
        except Exception:
            logger.exception("Could not prepare the SCAR email for %s", complaint.reference)
            raise WorkflowError("The SCAR email could not be prepared, so the SCAR was not issued.")
        complaint.cause = Complaint.Cause.EXTERNAL
        _move(complaint, actor, Action.SCAR_ISSUED, STATUS.PENDING_SCAR, comment=f"To {party_name} <{contact_email}>")
    return scar


def return_scar(complaint, actor, file):
    _require(can_return_scar(actor, complaint), "No SCAR is waiting to be returned on this complaint, or you are not assigned to it.")
    with transaction.atomic():
        _lock(complaint)
        _require(can_return_scar(actor, complaint), "The complaint has moved on.")
        scar = complaint.scars.last()
        attachment = Attachment.objects.create(
            complaint=complaint,
            section=Attachment.Section.SCAR,
            file=file,
            original_name=Path(file.name).name[:255],
            size=file.size,
            uploaded_by=actor,
        )
        scar.returned_file = attachment
        scar.returned_at = timezone.now()
        scar.returned_by = actor
        scar.save()
        _move(complaint, actor, Action.SCAR_RETURNED, STATUS.INVESTIGATION, comment=attachment.original_name)


# --- Step 5: the HOD ------------------------------------------------------------------------------


def hod_submit_to_fm(complaint, actor, decision, cause, reason="", comment=""):
    _require(can_review_as_hod(actor, complaint), "You cannot review this complaint as HOD now.")
    if decision not in Complaint.HodDecision.values:
        raise WorkflowError("Choose Valid or Invalid.")
    if cause not in Complaint.Cause.values:
        raise WorkflowError("Say whether the cause is internal or external.")
    if decision == Complaint.HodDecision.INVALID and not reason.strip():
        raise WorkflowError("Give the reason the complaint is invalid.")
    with transaction.atomic():
        _lock(complaint)
        _require(can_review_as_hod(actor, complaint), "The complaint has moved on.")
        complaint.hod_decision = decision
        complaint.cause = cause
        complaint.hod_reason = reason.strip() if decision == Complaint.HodDecision.INVALID else ""
        _move(complaint, actor, Action.HOD_TO_FM, STATUS.FM_REVIEW, comment=comment.strip())
    return _notify(emails.notify_fm_of_response, complaint, actor)


def hod_return(complaint, actor, comment):
    _require(can_review_as_hod(actor, complaint), "You cannot review this complaint as HOD now.")
    if not comment.strip():
        raise WorkflowError("Tell the PIC what to improve: a comment is needed to return the complaint.")
    with transaction.atomic():
        _lock(complaint)
        _require(can_review_as_hod(actor, complaint), "The complaint has moved on.")
        _move(complaint, actor, Action.HOD_RETURNED, STATUS.INVESTIGATION, comment=comment.strip())
    return _notify(emails.notify_pics_of_hod_return, complaint, actor, comment.strip())


# --- Step 6: the FM -------------------------------------------------------------------------------


def fm_decide(complaint, actor, approve, comment=""):
    _require(can_review_as_fm(actor, complaint), "You cannot approve or reject this complaint now.")
    if not approve and not comment.strip():
        raise WorkflowError("A comment is needed to reject the response.")
    with transaction.atomic():
        _lock(complaint)
        _require(can_review_as_fm(actor, complaint), "The complaint has moved on.")
        if approve:
            _move(complaint, actor, Action.FM_APPROVED, STATUS.APPROVED, comment=comment.strip())
        else:
            _move(complaint, actor, Action.FM_REJECTED, STATUS.INVESTIGATION, comment=comment.strip())
    return _notify(emails.notify_of_fm_decision, complaint, actor, approve, comment.strip())


# --- Step 7: the reply to the customer ---------------------------------------------------------------


def reply_problems(complaint):
    problems = []
    if complaint.scars.exists() and not complaint.scars.last().is_returned:
        problems.append("The reply is held until the returned SCAR has been uploaded.")
    if not complaint.contact_email:
        problems.append("The complaint has no customer email address.")
    return problems


def send_reply(complaint, actor, subject, body, files=()):
    """Email the customer, keep a copy, and set the status to Customer Response Sent."""
    try:
        return _send_reply(complaint, actor, subject, body, files)
    except emails.DeliveryError as error:
        emails.record_failure(error)  # written after the rollback, so the failure is kept
        raise WorkflowError("The email to the customer could not be sent, so nothing was changed. Try again later.")


def _send_reply(complaint, actor, subject, body, files):
    _require(can_reply(actor, complaint), "You cannot reply to the customer now.")
    if not subject.strip() or not body.strip():
        raise WorkflowError("The reply needs a subject and a message.")
    with transaction.atomic():
        _lock(complaint)
        _require(can_reply(actor, complaint), "The complaint has moved on.")
        problems = reply_problems(complaint)
        if problems:
            raise WorkflowError(problems)
        reply = CustomerReply(
            complaint=complaint, to_email=complaint.contact_email, subject=subject.strip(), body=body, sent_by=actor
        )
        mail_files = []
        for file in files:
            file.seek(0)
            mail_files.append((Path(file.name).name, file.read(), file.content_type or "application/octet-stream"))
            file.seek(0)
        try:
            emails.send_customer_reply(complaint, reply, mail_files, actor)
        except emails.DeliveryError:
            raise
        except Exception:
            logger.exception("Could not prepare the customer reply for %s", complaint.reference)
            raise WorkflowError("The email to the customer could not be prepared, so nothing was changed.")
        reply.sent_at = timezone.now()
        reply.save()
        store_files(complaint, files, actor, Attachment.Section.REPLY)
        _move(complaint, actor, Action.REPLY_SENT, STATUS.RESPONSE_SENT, comment=f"To {reply.to_email}")
    return _notify(emails.notify_of_reply_sent, complaint, actor)


# --- Step 8: effectiveness and closing ------------------------------------------------------------------


def save_verification(complaint, actor, first_date, first_result, second_date, second_result):
    _require(can_verify(actor, complaint), "Only the Coordinator can record the effectiveness check, after the reply was sent.")
    with transaction.atomic():
        _lock(complaint)
        _require(can_verify(actor, complaint), "The complaint has moved on.")
        verification, _ = Verification.objects.get_or_create(complaint=complaint)
        verification.first_verified_date = first_date
        verification.first_result = first_result
        verification.second_verified_date = second_date
        verification.second_result = second_result
        verification.save()
    return verification


def close_case(complaint, actor, comment=""):
    _require(can_verify(actor, complaint), "Only the Coordinator can close a case, after the reply was sent.")
    with transaction.atomic():
        _lock(complaint)
        _require(can_verify(actor, complaint), "The complaint has moved on.")
        verification = getattr(complaint, "verification", None)
        if not verification or not verification.first_verified_date or not verification.first_result.strip():
            raise WorkflowError("Record the first verified date and its result before closing the case.")
        verification.closed_at = timezone.now()
        verification.closed_by = actor
        verification.closing_comment = comment.strip()
        verification.save()
        _move(complaint, actor, Action.CLOSED, STATUS.CLOSED, comment=comment.strip())


def not_effective(complaint, actor, comment):
    _require(can_verify(actor, complaint), "Only the Coordinator can review the effectiveness, after the reply was sent.")
    if not comment.strip():
        raise WorkflowError("Say why the action was not effective: a comment is needed.")
    with transaction.atomic():
        _lock(complaint)
        _require(can_verify(actor, complaint), "The complaint has moved on.")
        _move(complaint, actor, Action.NOT_EFFECTIVE, STATUS.INVESTIGATION, comment=comment.strip())
    return _notify(emails.notify_pics_not_effective, complaint, actor, comment.strip())
