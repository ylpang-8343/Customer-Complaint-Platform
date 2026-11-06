"""The progress bar at the top of a complaint."""

import datetime
import re

from django.urls import reverse

from complaints import workflow
from complaints.models import Complaint
from complaints.progress import progress
from tests.test_complaints import as_user, lodge  # noqa: F401  (fixtures and helpers)
from tests.test_workflow import (  # noqa: F401
    approved,
    assigned,
    at_fm,
    at_hod,
    fresh,
    replied,
    submitted,
)


def states(complaint):
    return [(s["state"]) for s in progress(complaint, complaint.events.all())]


def test_each_status_marks_its_stage(as_user, user_of):
    complaint = submitted(as_user)
    assert states(complaint) == ["done", "current", "todo", "todo", "todo", "todo", "todo"]
    complaint = assigned(as_user, user_of)
    assert states(complaint) == ["done", "done", "current", "todo", "todo", "todo", "todo"]
    assert states(at_hod(as_user, user_of)) == ["done", "done", "done", "current", "todo", "todo", "todo"]
    assert states(at_fm(as_user, user_of)) == ["done", "done", "done", "done", "current", "todo", "todo"]
    assert states(approved(as_user, user_of)) == ["done", "done", "done", "done", "done", "current", "todo"]
    assert states(replied(as_user, user_of)) == ["done", "done", "done", "done", "done", "done", "current"]


def test_a_closed_complaint_has_every_stage_done(as_user, user_of):
    complaint = replied(as_user, user_of)
    workflow.save_verification(complaint, user_of("mr"), None, "", None, "")
    workflow.save_verification(fresh(complaint), user_of("mr"), datetime.date(2026, 12, 1), "ok", None, "")
    workflow.close_case(fresh(complaint), user_of("mr"))
    steps = progress(fresh(complaint), fresh(complaint).events.all())
    assert [s["state"] for s in steps] == ["done"] * 7
    assert all(s["note"] for s in steps)  # each shows when it was done


def test_the_notes_say_who_it_is_waiting_for(as_user, user_of):
    complaint = submitted(as_user)
    current = [s for s in progress(complaint, complaint.events.all()) if s["state"] == "current"][0]
    assert current["label"] == "Assignment" and current["note"] == "Waiting for the Coordinator"
    assert [s for s in progress(at_hod(as_user, user_of), [])][3]["note"] == "Waiting for the HOD"


def test_a_return_moves_the_marker_back_and_says_why(as_user, user_of):
    complaint = at_hod(as_user, user_of)
    workflow.hod_return(complaint, user_of("fa.hod"), "Too vague")
    complaint = fresh(complaint)
    steps = progress(complaint, complaint.events.all())
    assert [s["state"] for s in steps] == ["done", "done", "current", "todo", "todo", "todo", "todo"]
    assert steps[2]["note"] == "Returned by the HOD"


def test_a_pending_scar_stays_on_the_investigation_stage(as_user, user_of):
    complaint = assigned(as_user, user_of)
    workflow.issue_scar(
        complaint, user_of("fa.pic"), "supplier", "Seal Makers Ltd", "q@sealmakers.example", "SCAR", "Please investigate"
    )
    steps = progress(fresh(complaint), [])
    assert steps[2]["state"] == "current" and steps[2]["note"] == "Pending SCAR from supplier"


def test_the_detail_page_shows_the_progress_bar(as_user, user_of):
    complaint = at_hod(as_user, user_of)
    html = as_user("fa.fm").get(reverse("complaint_detail", args=[complaint.pk])).content.decode()
    assert 'class="stepper"' in html and 'aria-current="step"' in html
    assert re.search(r'<li class="current" aria-current="step">.*?HOD review', html, re.S)
    assert "Raised by" in html and "Sample Sales" in html
    draft = Complaint.objects.create(lodged_by=user_of("sales"), factory=complaint.factory)
    draft_html = as_user("sales").get(reverse("complaint_detail", args=[draft.pk])).content.decode()
    assert "Draft, with the lodger" in draft_html
