"""The progress bar shown at the top of a complaint: where it is in the process (proposal section 4)."""

from django.utils import formats, timezone

from .models import Complaint

STATUS = Complaint.Status

# (label, the history action that completes it, whether to use the first such event or the last)
STAGES = [
    ("Submitted", "submitted", "first"),
    ("Assignment", "assigned", "first"),
    ("Investigation", "sent_to_hod", "last"),
    ("HOD review", "hod_to_fm", "last"),
    ("FM approval", "fm_approved", "last"),
    ("Customer reply", "reply_sent", "last"),
    ("Effectiveness and closing", "closed", "last"),
]

# The stage a complaint is at, by status (1-based). A closed complaint has completed every stage.
CURRENT_STAGE = {
    STATUS.DRAFT: 1,
    STATUS.SUBMITTED: 2,
    STATUS.INVESTIGATION: 3,
    STATUS.PENDING_SCAR: 3,
    STATUS.HOD_REVIEW: 4,
    STATUS.FM_REVIEW: 5,
    STATUS.APPROVED: 6,
    STATUS.RESPONSE_SENT: 7,
}

WAITING = {
    STATUS.DRAFT: "Draft, with the lodger",
    STATUS.SUBMITTED: "Waiting for the Coordinator",
    STATUS.INVESTIGATION: "Waiting for the PIC",
    STATUS.PENDING_SCAR: "Pending SCAR from supplier",
    STATUS.HOD_REVIEW: "Waiting for the HOD",
    STATUS.FM_REVIEW: "Waiting for the FM",
    STATUS.APPROVED: "Approved, PIC to reply",
    STATUS.RESPONSE_SENT: "Waiting for the Coordinator",
}

RETURNED = {
    "hod_returned": "Returned by the HOD",
    "fm_rejected": "Rejected by the FM",
    "not_effective": "Not effective, back with the PIC",
}


def _when(value):
    return formats.date_format(timezone.localtime(value), "j M, H:i") if value else ""


def progress(complaint, events):
    """Return [{number, label, state, note}, ...]; state is done, current or todo.

    ``events`` is the complaint's history, oldest first.
    """
    events = list(events)
    current = CURRENT_STAGE.get(complaint.status)  # None once closed
    returned = ""
    if events and complaint.status == STATUS.INVESTIGATION:
        returned = RETURNED.get(events[-1].action, "")
    steps = []
    for number, (label, action, which) in enumerate(STAGES, start=1):
        matching = [e for e in events if e.action == action]
        if current is None or number < current:
            state = "done"
            event = (matching[0] if which == "first" else matching[-1]) if matching else None
            note = _when(event.created_at) if event else ""
        elif number == current:
            state = "current"
            note = returned or WAITING[complaint.status]
        else:
            state, note = "todo", ""
        steps.append({"number": number, "label": label, "state": state, "note": note})
    return steps
