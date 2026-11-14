"""Steps 3 to 8: assignment, investigation, SCAR, HOD, FM, reply, closing, deleting."""

import datetime

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from accounts.models import Factory, FactoryPIC
from complaints import people, workflow
from complaints.models import Attachment, Complaint, ComplaintEvent, LabTest, Scar
from complaints.workflow import WorkflowError
from tests.test_complaints import as_user, days_ago, lodge  # noqa: F401  (fixtures and helpers)

S = Complaint.Status


# --- Helpers: bring a complaint to a stage ---------------------------------------------------


def fresh(complaint):
    return Complaint.objects.get(pk=complaint.pk)


def submitted(as_user, **overrides):
    lodge(as_user("sales"), submit="1", **overrides)
    return Complaint.objects.latest("id")


def pic_entries(*usernames, factory="FA"):
    return list(FactoryPIC.objects.filter(factory__code=factory, user__username__in=usernames))


def assigned(as_user, user_of, *usernames, **overrides):
    complaint = submitted(as_user, **overrides)
    workflow.assign(complaint, user_of("mr"), pic_entries(*(usernames or ("fa.pic",))), "Please investigate")
    return fresh(complaint)


def write_findings(complaint, user, cause="internal"):
    workflow.save_investigation(
        complaint, user, "Seal failed", "Replaced the pack", "Changed the supplier of seals", cause
    )


def at_hod(as_user, user_of, **overrides):
    complaint = assigned(as_user, user_of, **overrides)
    pic = user_of("fa.pic")
    write_findings(complaint, pic)
    workflow.submit_to_hod(complaint, pic)
    return fresh(complaint)


def at_fm(as_user, user_of, decision="valid"):
    complaint = at_hod(as_user, user_of)
    workflow.hod_submit_to_fm(complaint, user_of("fa.hod"), decision, "internal", reason="Not our fault" if decision == "invalid" else "")
    return fresh(complaint)


def approved(as_user, user_of):
    complaint = at_fm(as_user, user_of)
    workflow.fm_decide(complaint, user_of("fa.fm"), True, "Good work")
    return fresh(complaint)


def replied(as_user, user_of):
    complaint = approved(as_user, user_of)
    workflow.send_reply(complaint, user_of("fa.pic"), "Re: your complaint", "Dear customer, sorry.")
    return fresh(complaint)


def actions(user, complaint):
    return [a["url_name"] for a in workflow.available_actions(user, complaint)]


# --- Step 3: assignment ------------------------------------------------------------------------


def test_the_coordinator_assigns_pics_and_each_is_emailed(as_user, user_of):
    complaint = submitted(as_user)
    mail.outbox.clear()
    response = as_user("mr").post(
        reverse("complaint_assign", args=[complaint.pk]),
        {"pics": [e.pk for e in pic_entries("fa.pic", "fa.labmgr")], "remarks": "Urgent, please"},
    )
    assert response.status_code == 302
    complaint = fresh(complaint)
    assert complaint.status == S.INVESTIGATION
    assert complaint.received_by == user_of("mr")
    assert complaint.acknowledged_at is not None
    assert complaint.coordinator_remarks == "Urgent, please"
    assert {a.user.username for a in complaint.assignments.all()} == {"fa.pic", "fa.labmgr"}

    assert len(mail.outbox) == 1
    email = mail.outbox[0]
    assert set(email.to) == {"fa.pic@example.com", "fa.labmgr@example.com"}
    assert complaint.reference in email.subject and complaint.reference in email.body
    assert f"/complaints/{complaint.pk}/" in email.body and "Urgent, please" in email.body
    (name, content, mime), = email.attachments
    assert name.endswith("summary.pdf") and mime == "application/pdf" and content.startswith(b"%PDF")


def test_only_the_coordinator_can_assign(as_user, user_of):
    complaint = submitted(as_user)
    entries = [e.pk for e in pic_entries("fa.pic")]
    for username in ("fa.pic", "fa.hod", "fa.fm", "sales", "management"):
        client = as_user(username)
        assert client.get(reverse("complaint_assign", args=[complaint.pk])).status_code in (302, 404)
        client.post(reverse("complaint_assign", args=[complaint.pk]), {"pics": entries})
    assert fresh(complaint).status == S.SUBMITTED
    with pytest.raises(WorkflowError):
        workflow.assign(complaint, user_of("fa.pic"), pic_entries("fa.pic"))


def test_a_pic_of_another_factory_cannot_be_chosen(as_user, user_of):
    complaint = submitted(as_user)
    foreign = FactoryPIC.objects.get(factory__code="FB", user__username="fb.pic")
    response = as_user("mr").post(reverse("complaint_assign", args=[complaint.pk]), {"pics": [foreign.pk]})
    assert response.status_code == 200  # the form is shown again with an error
    assert fresh(complaint).status == S.SUBMITTED
    with pytest.raises(WorkflowError):
        workflow.assign(complaint, user_of("mr"), [foreign])


def test_at_least_one_pic_is_needed(as_user):
    complaint = submitted(as_user)
    response = as_user("mr").post(reverse("complaint_assign", args=[complaint.pk]), {"remarks": "x"})
    assert response.status_code == 200 and fresh(complaint).status == S.SUBMITTED


def test_a_sample_complaint_needs_a_laboratory_pic(as_user):
    complaint = submitted(as_user, sample_sent="True")
    client = as_user("mr")
    url = reverse("complaint_assign", args=[complaint.pk])
    refused = client.post(url, {"pics": [e.pk for e in pic_entries("fa.pic")]})
    assert refused.status_code == 200 and "laboratory PIC" in refused.content.decode()
    ok = client.post(url, {"pics": [e.pk for e in pic_entries("fa.pic", "fa.lab")]})
    assert ok.status_code == 302 and fresh(complaint).status == S.INVESTIGATION


def test_changing_the_pics_emails_only_the_new_one(as_user, user_of):
    complaint = assigned(as_user, user_of, "fa.pic")
    mail.outbox.clear()
    workflow.assign(complaint, user_of("mr"), pic_entries("fa.pic", "fa.labmgr"), "Added the lab manager")
    assert [m.to for m in mail.outbox] == [["fa.labmgr@example.com"]]
    complaint = fresh(complaint)
    assert complaint.status == S.INVESTIGATION and complaint.assignments.count() == 2
    workflow.assign(complaint, user_of("mr"), pic_entries("fa.labmgr"))
    assert [a.user.username for a in fresh(complaint).assignments.all()] == ["fa.labmgr"]


def test_a_failed_email_does_not_undo_the_assignment(as_user, user_of, monkeypatch):
    complaint = submitted(as_user)

    def broken(*args, **kwargs):
        raise OSError("mail server down")

    monkeypatch.setattr("complaints.emails.EmailMessage.send", broken)
    response = as_user("mr").post(
        reverse("complaint_assign", args=[complaint.pk]), {"pics": [e.pk for e in pic_entries("fa.pic")]}, follow=True
    )
    assert fresh(complaint).status == S.INVESTIGATION
    assert "could not be sent" in response.content.decode()


# --- Step 4: investigation, laboratory ------------------------------------------------------------


def test_only_assigned_pics_can_investigate(as_user, user_of):
    complaint = assigned(as_user, user_of, "fa.pic")
    assert "complaint_investigation" in actions(user_of("fa.pic"), complaint)
    for username in ("fa.labmgr", "fb.pic", "fa.hod", "mr", "sales"):  # same factory but not assigned, others
        user = user_of(username)
        assert "complaint_investigation" not in actions(user, complaint), username
        with pytest.raises(WorkflowError):
            write_findings(complaint, user)
    page = as_user("fa.labmgr").get(reverse("complaint_investigation", args=[complaint.pk]))
    assert page.status_code == 302


def test_findings_are_saved_without_being_complete(as_user, user_of):
    complaint = assigned(as_user, user_of)
    client = as_user("fa.pic")
    response = client.post(
        reverse("complaint_investigation", args=[complaint.pk]), {"save": "1", "root_cause": "Half done"}
    )
    assert response.status_code == 302
    complaint = fresh(complaint)
    assert complaint.investigation.root_cause == "Half done" and complaint.status == S.INVESTIGATION


def test_submit_to_hod_lists_what_is_missing(as_user, user_of):
    complaint = assigned(as_user, user_of)
    client = as_user("fa.pic")
    response = client.post(reverse("complaint_investigation", args=[complaint.pk]), {"submit": "1", "root_cause": "x"})
    html = response.content.decode()
    assert response.status_code == 200
    assert "Enter the correction" in html and "corrective and preventive action" in html and "internal or external" in html
    assert "Your changes are saved" in html
    assert fresh(complaint).status == S.INVESTIGATION
    assert fresh(complaint).investigation.root_cause == "x"


def test_submitting_findings_moves_to_the_hod_and_emails_them(as_user, user_of):
    complaint = assigned(as_user, user_of)
    mail.outbox.clear()
    client = as_user("fa.pic")
    data = {"submit": "1", "root_cause": "Seal failed", "correction": "Replaced", "corrective_action": "New seals", "cause": "internal"}
    assert client.post(reverse("complaint_investigation", args=[complaint.pk]), data).status_code == 302
    complaint = fresh(complaint)
    assert complaint.status == S.HOD_REVIEW
    assert complaint.investigation.respondent == user_of("fa.pic") and complaint.investigation.submitted_at
    assert [m.to for m in mail.outbox] == [["fa.hod@example.com"]]
    assert complaint.reference in mail.outbox[0].subject
    # The PIC can no longer change the findings while the HOD reviews them.
    assert "complaint_investigation" not in actions(user_of("fa.pic"), complaint)


def test_the_laboratory_section_gates_the_findings_when_a_sample_was_sent(as_user, user_of):
    complaint = assigned(as_user, user_of, "fa.pic", "fa.lab", sample_sent="True")
    pic, lab = user_of("fa.pic"), user_of("fa.lab")
    write_findings(complaint, pic)
    with pytest.raises(WorkflowError) as error:
        workflow.submit_to_hod(complaint, pic)
    assert "laboratory section" in str(error.value)

    # Only laboratory staff fill the section in, not the other PIC.
    assert "complaint_lab" not in actions(pic, complaint) and "complaint_lab" in actions(lab, complaint)
    with pytest.raises(WorkflowError):
        workflow.save_lab(complaint, pic, datetime.date(2026, 11, 2), "", {})

    ffa = LabTest.objects.get(name="FFA")
    workflow.save_lab(complaint, lab, datetime.date(2026, 11, 2), "Checked", {ffa: ("0.4%", "0.5%")})
    lab_section = fresh(complaint).lab
    assert lab_section.tested_by == lab and lab_section.acknowledged_on == datetime.date(2026, 11, 2)
    assert lab_section.results.get(test=ffa).retained_sample == "0.5%"

    workflow.submit_to_hod(fresh(complaint), pic)
    assert fresh(complaint).status == S.HOD_REVIEW


def test_a_date_without_any_result_is_not_enough(as_user, user_of):
    complaint = assigned(as_user, user_of, "fa.pic", "fa.lab", sample_sent="True")
    write_findings(complaint, user_of("fa.pic"))
    workflow.save_lab(complaint, user_of("fa.lab"), datetime.date(2026, 11, 2), "", {})
    with pytest.raises(WorkflowError) as error:
        workflow.submit_to_hod(fresh(complaint), user_of("fa.pic"))
    assert "at least one test result" in str(error.value)


def test_the_lab_form_saves_the_grid(as_user, user_of):
    complaint = assigned(as_user, user_of, "fa.pic", "fa.lab", sample_sent="True")
    client = as_user("fa.lab")
    url = reverse("complaint_lab", args=[complaint.pk])
    assert client.get(url).status_code == 200
    ffa, colour = LabTest.objects.get(name="FFA"), LabTest.objects.get(name="Colour")
    client.post(url, {"acknowledged_on": "2026-11-02", "remarks": "ok", f"buyer_{ffa.pk}": "0.3", f"retained_{colour.pk}": "Pale"})
    lab = fresh(complaint).lab
    assert lab.results.get(test=ffa).buyer_sample == "0.3" and lab.results.get(test=colour).retained_sample == "Pale"
    assert as_user("fa.pic").get(url).status_code == 302


# --- Step 4a: SCAR ---------------------------------------------------------------------------------


def issue(complaint, user, **overrides):
    data = {
        "party_type": "supplier", "party_name": "Seal Makers Ltd", "contact_email": "quality@sealmakers.example",
        "subject": f"SCAR {complaint.reference}", "message": "Please investigate.",
    }  # fmt: skip
    data.update(overrides)
    return workflow.issue_scar(complaint, user, **data)


def test_issuing_a_scar_emails_the_party_and_locks_submit(as_user, user_of):
    complaint = assigned(as_user, user_of)
    pic = user_of("fa.pic")
    write_findings(complaint, pic, cause="external")
    mail.outbox.clear()
    response = as_user("fa.pic").post(
        reverse("complaint_scar", args=[complaint.pk]),
        {"party_type": "supplier", "party_name": "Seal Makers Ltd", "contact_email": "quality@sealmakers.example",
         "subject": "SCAR", "message": "Please investigate"},
    )  # fmt: skip
    assert response.status_code == 302
    complaint = fresh(complaint)
    assert complaint.status == S.PENDING_SCAR and complaint.cause == "external"
    email = mail.outbox[0]
    assert email.to == ["quality@sealmakers.example"] and email.cc == ["fa.pic@example.com"]
    assert email.reply_to == ["fa.pic@example.com"]
    assert email.attachments[0][0].startswith("SCAR-") and email.attachments[0][2] == "application/pdf"

    with pytest.raises(WorkflowError) as error:
        workflow.submit_to_hod(complaint, pic)
    assert "locked until the returned SCAR is uploaded" in str(error.value)
    assert "Pending SCAR from Supplier" in as_user("fa.pic").get(reverse("complaint_detail", args=[complaint.pk])).content.decode()


def test_the_scar_text_is_built_from_the_template_and_can_be_generated_again(as_user, user_of):
    complaint = assigned(as_user, user_of)
    client = as_user("fa.pic")
    url = reverse("complaint_scar", args=[complaint.pk])
    html = client.get(url).content.decode()
    assert complaint.reference in html and "Supplier Corrective Action Request" in html
    again = client.post(url, {"regenerate": "1", "party_name": "Seal Makers Ltd", "message": "edited text"}).content.decode()
    assert "Dear Seal Makers Ltd," in again and "edited text" not in again


def test_the_scar_pdf_is_available_to_people_who_can_see_the_complaint(as_user, user_of):
    complaint = assigned(as_user, user_of)
    scar = issue(complaint, user_of("fa.pic"))
    response = as_user("fa.hod").get(reverse("scar_pdf", args=[scar.pk]))
    assert response.status_code == 200 and response.content.startswith(b"%PDF")
    assert as_user("fb.pic").get(reverse("scar_pdf", args=[scar.pk])).status_code == 404


def test_a_scar_that_cannot_be_emailed_is_not_issued(as_user, user_of, monkeypatch):
    complaint = assigned(as_user, user_of)

    def broken(*args, **kwargs):
        raise OSError("mail server down")

    monkeypatch.setattr("complaints.emails.EmailMessage.send", broken)
    with pytest.raises(WorkflowError) as error:
        issue(complaint, user_of("fa.pic"))
    assert "was not issued" in str(error.value)
    assert fresh(complaint).status == S.INVESTIGATION and not Scar.objects.exists()
    assert not ComplaintEvent.objects.filter(action="scar_issued").exists()


def test_uploading_the_returned_scar_unlocks_submit(as_user, user_of):
    complaint = assigned(as_user, user_of)
    pic = user_of("fa.pic")
    write_findings(complaint, pic, cause="external")
    issue(complaint, pic)
    client = as_user("fa.pic")
    url = reverse("complaint_scar_return", args=[complaint.pk])
    assert client.get(url).status_code == 200
    bad = client.post(url, {"file": SimpleUploadedFile("virus.exe", b"x")})
    assert bad.status_code == 200 and fresh(complaint).status == S.PENDING_SCAR
    client.post(url, {"file": SimpleUploadedFile("returned-scar.pdf", b"%PDF-1.4 returned")})
    complaint = fresh(complaint)
    scar = complaint.scars.get()
    assert complaint.status == S.INVESTIGATION and scar.is_returned and scar.returned_file.original_name == "returned-scar.pdf"
    workflow.submit_to_hod(complaint, pic)
    assert fresh(complaint).status == S.HOD_REVIEW
    # The returned SCAR is downloadable by the people who can see the complaint.
    download = as_user("fa.fm").get(reverse("attachment_download", args=[scar.returned_file.pk]))
    assert download.status_code == 200 and b"".join(download.streaming_content) == b"%PDF-1.4 returned"


def test_only_an_assigned_pic_can_handle_the_scar(as_user, user_of):
    complaint = assigned(as_user, user_of)
    for username in ("fa.labmgr", "fa.hod", "mr"):
        assert "complaint_scar" not in actions(user_of(username), complaint)
        with pytest.raises(WorkflowError):
            issue(complaint, user_of(username))


# --- Step 5: HOD ----------------------------------------------------------------------------------------


def test_the_hod_submits_straight_to_the_fm_with_copies(as_user, user_of):
    complaint = at_hod(as_user, user_of)
    mail.outbox.clear()
    response = as_user("fa.hod").post(
        reverse("complaint_hod", args=[complaint.pk]),
        {"submit": "1", "decision": "valid", "cause": "internal", "comment": "Agreed"},
    )
    assert response.status_code == 302
    complaint = fresh(complaint)
    assert complaint.status == S.FM_REVIEW and complaint.hod_decision == "valid" and complaint.cause == "internal"
    email = mail.outbox[0]
    assert email.to == ["fa.fm@example.com"]
    assert set(email.cc) == {"mr@example.com", "fa.pic@example.com"}  # not through the Coordinator, but copied
    assert "Valid" in email.body


def test_an_invalid_complaint_needs_a_reason(as_user, user_of):
    complaint = at_hod(as_user, user_of)
    client = as_user("fa.hod")
    url = reverse("complaint_hod", args=[complaint.pk])
    refused = client.post(url, {"submit": "1", "decision": "invalid", "cause": "external"})
    assert refused.status_code == 200 and "Give the reason" in refused.content.decode()
    assert fresh(complaint).status == S.HOD_REVIEW
    client.post(url, {"submit": "1", "decision": "invalid", "cause": "external", "reason": "Customer misused the product"})
    complaint = fresh(complaint)
    assert complaint.status == S.FM_REVIEW and complaint.hod_reason == "Customer misused the product"


def test_the_hod_decision_and_cause_are_needed(as_user, user_of):
    complaint = at_hod(as_user, user_of)
    response = as_user("fa.hod").post(reverse("complaint_hod", args=[complaint.pk]), {"submit": "1"})
    html = response.content.decode()
    assert "Choose Valid or Invalid" in html and "internal or external" in html


def test_the_hod_returns_the_complaint_to_the_pic_with_a_comment(as_user, user_of):
    complaint = at_hod(as_user, user_of)
    client = as_user("fa.hod")
    url = reverse("complaint_hod", args=[complaint.pk])
    assert "Tell the PIC" in client.post(url, {"return": "1"}).content.decode()
    mail.outbox.clear()
    client.post(url, {"return": "1", "comment": "Root cause is too vague"})
    complaint = fresh(complaint)
    assert complaint.status == S.INVESTIGATION
    assert [m.to for m in mail.outbox] == [["fa.pic@example.com"]] and "Root cause is too vague" in mail.outbox[0].body
    # The PIC improves the response and submits it again.
    workflow.submit_to_hod(complaint, user_of("fa.pic"))
    assert fresh(complaint).status == S.HOD_REVIEW


def test_only_the_hod_of_the_factory_can_review(as_user, user_of):
    complaint = at_hod(as_user, user_of)
    for username in ("fb.hod", "fa.fm", "fa.pic", "mr", "management"):
        assert "complaint_hod" not in actions(user_of(username), complaint), username
        with pytest.raises(WorkflowError):
            workflow.hod_return(complaint, user_of(username), "no")
    assert as_user("fb.hod").get(reverse("complaint_hod", args=[complaint.pk])).status_code == 404
    assert "complaint_hod" in actions(user_of("fa.hod"), complaint)


def test_a_deputy_acts_for_a_hod_who_is_away(as_user, user_of):
    hod, deputy = user_of("fa.hod"), user_of("fb.hod")
    hod.deputy, hod.away_until = deputy, datetime.date.today() + datetime.timedelta(days=3)
    hod.save()
    mail.outbox.clear()
    complaint = at_hod(as_user, user_of)
    assert any(m.to == ["fb.hod@example.com"] for m in mail.outbox)  # the deputy is emailed instead
    assert "complaint_hod" in actions(deputy, complaint)
    assert Complaint.objects.visible_to(deputy).filter(pk=complaint.pk).exists()
    assert as_user("fb.hod").get(reverse("complaint_hod", args=[complaint.pk])).status_code == 200
    workflow.hod_submit_to_fm(complaint, deputy, "valid", "internal")
    assert fresh(complaint).status == S.FM_REVIEW
    # Once the HOD is back, the deputy no longer acts for them or sees their factory.
    hod.away_until = datetime.date.today() - datetime.timedelta(days=1)
    hod.save()
    assert not Complaint.objects.visible_to(user_of("fb.hod")).filter(pk=complaint.pk).exists()


# --- Step 6: FM -----------------------------------------------------------------------------------------


def test_the_fm_approves_and_the_others_are_told(as_user, user_of):
    complaint = at_fm(as_user, user_of)
    mail.outbox.clear()
    response = as_user("fa.fm").post(reverse("complaint_fm", args=[complaint.pk]), {"approve": "1", "comment": "Well done"})
    assert response.status_code == 302
    assert fresh(complaint).status == S.APPROVED
    email = mail.outbox[0]
    assert set(email.to) == {"fa.pic@example.com", "fa.hod@example.com", "mr@example.com"}
    assert "Well done" in email.body and "approved" in email.subject


def test_a_rejection_needs_a_comment_and_goes_back_through_the_pic_and_hod(as_user, user_of):
    complaint = at_fm(as_user, user_of)
    client = as_user("fa.fm")
    url = reverse("complaint_fm", args=[complaint.pk])
    assert "A comment is needed" in client.post(url, {"reject": "1"}).content.decode()
    assert fresh(complaint).status == S.FM_REVIEW

    mail.outbox.clear()
    client.post(url, {"reject": "1", "comment": "Corrective action is not specific"})
    complaint = fresh(complaint)
    assert complaint.status == S.INVESTIGATION
    assert set(mail.outbox[0].to) == {"fa.pic@example.com", "fa.hod@example.com", "mr@example.com"}
    assert "Corrective action is not specific" in mail.outbox[0].body

    # The loop repeats until the FM approves.
    pic, hod, fm = user_of("fa.pic"), user_of("fa.hod"), user_of("fa.fm")
    workflow.submit_to_hod(complaint, pic)
    workflow.hod_submit_to_fm(fresh(complaint), hod, "valid", "internal")
    workflow.fm_decide(fresh(complaint), fm, False, "Still not enough")
    workflow.submit_to_hod(fresh(complaint), pic)
    workflow.hod_submit_to_fm(fresh(complaint), hod, "valid", "internal")
    workflow.fm_decide(fresh(complaint), fm, True)
    assert fresh(complaint).status == S.APPROVED
    assert list(fresh(complaint).events.values_list("action", flat=True)).count("fm_rejected") == 2


def test_only_the_fm_of_the_factory_can_decide(as_user, user_of):
    complaint = at_fm(as_user, user_of)
    for username in ("fb.fm", "fa.hod", "fa.pic", "mr", "management"):
        assert "complaint_fm" not in actions(user_of(username), complaint), username
        with pytest.raises(WorkflowError):
            workflow.fm_decide(complaint, user_of(username), True)
    assert fresh(complaint).status == S.FM_REVIEW


def test_a_deputy_fm_acts_while_the_fm_is_away(as_user, user_of):
    fm, deputy = user_of("fa.fm"), user_of("fa.hod")
    fm.deputy, fm.away_until = deputy, datetime.date.today() + datetime.timedelta(days=2)
    fm.save()
    mail.outbox.clear()
    complaint = at_fm(as_user, user_of)
    assert any(m.to == ["fa.hod@example.com"] for m in mail.outbox if "approval" in m.subject)
    assert "complaint_fm" in actions(deputy, complaint)
    workflow.fm_decide(complaint, deputy, True)
    assert fresh(complaint).status == S.APPROVED


# --- Step 7: reply to the customer ------------------------------------------------------------------------


def test_the_reply_is_built_from_the_findings_and_the_approval(as_user, user_of):
    complaint = approved(as_user, user_of)
    html = as_user("fa.pic").get(reverse("complaint_reply", args=[complaint.pk])).content.decode()
    # The root cause and the corrective action come from the findings; the signature is the approving FM.
    assert "Seal failed" in html and "Changed the supplier of seals" in html
    assert "Investigation Findings for Complaint" in html and "Sample FM FA" in html and "Factory Manager, Factory A" in html


def test_sending_the_reply_keeps_a_copy_and_notifies_the_others(as_user, user_of):
    complaint = approved(as_user, user_of)
    mail.outbox.clear()
    response = as_user("fa.pic").post(
        reverse("complaint_reply", args=[complaint.pk]),
        {"subject": "Re: your complaint", "body": "Dear customer, we are sorry.", "attachments": SimpleUploadedFile("report.pdf", b"%PDF-1.4 report")},
    )
    assert response.status_code == 302
    complaint = fresh(complaint)
    assert complaint.status == S.RESPONSE_SENT
    assert complaint.reply.body == "Dear customer, we are sorry." and complaint.reply.sent_by == user_of("fa.pic")
    assert complaint.reply.to_email == "contact@example.com"
    customer, notice = mail.outbox
    assert customer.to == ["contact@example.com"] and customer.cc == ["fa.pic@example.com"]
    assert customer.attachments[0][0] == "report.pdf"
    assert set(notice.to) == {"fa.hod@example.com", "fa.fm@example.com", "mr@example.com"}
    assert complaint.attachments.filter(section="reply").count() == 1


def test_only_the_assigned_pic_can_reply_and_only_after_approval(as_user, user_of):
    complaint = at_fm(as_user, user_of)
    assert "complaint_reply" not in actions(user_of("fa.pic"), complaint)
    with pytest.raises(WorkflowError):
        workflow.send_reply(complaint, user_of("fa.pic"), "s", "b")
    workflow.fm_decide(complaint, user_of("fa.fm"), True)
    complaint = fresh(complaint)
    for username in ("fa.labmgr", "fa.hod", "mr"):
        assert "complaint_reply" not in actions(user_of(username), complaint)
        with pytest.raises(WorkflowError):
            workflow.send_reply(complaint, user_of(username), "s", "b")


def test_the_reply_is_held_until_the_scar_is_back(as_user, user_of):
    complaint = approved(as_user, user_of)
    Scar.objects.create(
        complaint=complaint, party_name="Seal Makers Ltd", contact_email="q@sealmakers.example",
        subject="SCAR", message="m", issued_by=user_of("fa.pic"),
    )  # fmt: skip
    client = as_user("fa.pic")
    url = reverse("complaint_reply", args=[complaint.pk])
    assert "held until the returned SCAR" in client.get(url).content.decode()
    client.post(url, {"subject": "s", "body": "b"})
    assert fresh(complaint).status == S.APPROVED
    with pytest.raises(WorkflowError):
        workflow.send_reply(complaint, user_of("fa.pic"), "s", "b")


def test_a_reply_that_cannot_be_emailed_changes_nothing(as_user, user_of, monkeypatch):
    complaint = approved(as_user, user_of)

    def broken(*args, **kwargs):
        raise OSError("mail server down")

    monkeypatch.setattr("complaints.emails.EmailMessage.send", broken)
    with pytest.raises(WorkflowError):
        workflow.send_reply(complaint, user_of("fa.pic"), "s", "b")
    complaint = fresh(complaint)
    assert complaint.status == S.APPROVED and not hasattr(complaint, "reply")


# --- Step 8: effectiveness and closing ---------------------------------------------------------------------------


def test_the_coordinator_records_the_checks_and_closes_the_case(as_user, user_of):
    complaint = replied(as_user, user_of)
    client = as_user("mr")
    url = reverse("complaint_verify", args=[complaint.pk])
    assert client.get(url).status_code == 200
    refused = client.post(url, {"close": "1"})
    assert "needed to close the case" in refused.content.decode() and fresh(complaint).status == S.RESPONSE_SENT

    client.post(url, {"save": "1", "first_verified_date": days_ago(30), "first_result": "No repeat"})
    assert fresh(complaint).status == S.RESPONSE_SENT and fresh(complaint).verification.first_result == "No repeat"
    client.post(url, {"close": "1", "first_verified_date": days_ago(30), "first_result": "No repeat", "second_verified_date": days_ago(10), "second_result": "Still fine", "comment": "Effective"})
    complaint = fresh(complaint)
    verification = complaint.verification
    assert complaint.status == S.CLOSED and verification.closed_by == user_of("mr") and verification.closed_at
    assert verification.second_verified_date == datetime.date.fromisoformat(days_ago(10)) and verification.closing_comment == "Effective"
    assert actions(user_of("mr"), complaint) == ["complaint_delete"]


def test_a_second_check_before_the_first_is_refused(as_user, user_of):
    complaint = replied(as_user, user_of)
    response = as_user("mr").post(
        reverse("complaint_verify", args=[complaint.pk]),
        {"save": "1", "first_verified_date": days_ago(30), "first_result": "x", "second_verified_date": days_ago(60)},
    )
    assert "cannot be before the first" in response.content.decode()


def test_not_effective_sends_the_case_back_to_the_pic(as_user, user_of):
    complaint = replied(as_user, user_of)
    client = as_user("mr")
    url = reverse("complaint_verify", args=[complaint.pk])
    assert "Say why" in client.post(url, {"not_effective": "1"}).content.decode()
    mail.outbox.clear()
    client.post(url, {"not_effective": "1", "first_verified_date": days_ago(30), "first_result": "Repeated", "comment": "Same defect again"})
    complaint = fresh(complaint)
    assert complaint.status == S.INVESTIGATION
    assert mail.outbox[0].to == ["fa.pic@example.com"] and "Same defect again" in mail.outbox[0].body
    assert complaint.verification.first_result == "Repeated"
    # Back to the PIC, so the cycle can run again, and the reply is already on record.
    assert "complaint_investigation" in actions(user_of("fa.pic"), complaint)


def test_only_the_coordinator_can_close(as_user, user_of):
    complaint = replied(as_user, user_of)
    for username in ("fa.pic", "fa.hod", "fa.fm", "management", "sales"):
        assert "complaint_verify" not in actions(user_of(username), complaint)
        with pytest.raises(WorkflowError):
            workflow.close_case(complaint, user_of(username))
    assert as_user("fa.pic").get(reverse("complaint_verify", args=[complaint.pk])).status_code == 302


# --- Deleting -------------------------------------------------------------------------------------------------------


def test_deleting_marks_the_complaint_and_keeps_it_in_the_log(as_user, user_of):
    complaint = assigned(as_user, user_of)
    client = as_user("mr")
    url = reverse("complaint_delete", args=[complaint.pk])
    assert "This field is required" in client.post(url, {"reason": ""}).content.decode()
    client.post(url, {"reason": "Duplicate of FA/2026/0009"})
    complaint = fresh(complaint)
    assert complaint.is_deleted and complaint.deleted_by == user_of("mr") and complaint.deleted_reason == "Duplicate of FA/2026/0009"
    assert complaint.status == S.INVESTIGATION  # the stage it reached is kept
    home = client.get(reverse("home"), {"status": "deleted"}).content.decode()
    assert complaint.reference in home and "Deleted" in home
    assert complaint.reference not in client.get(reverse("home"), {"status": "investigation"}).content.decode()
    # Nothing more can be done with it.
    assert actions(user_of("fa.pic"), complaint) == [] and actions(user_of("mr"), complaint) == []
    with pytest.raises(WorkflowError):
        write_findings(complaint, user_of("fa.pic"))
    assert "Deleted" in client.get(reverse("complaint_detail", args=[complaint.pk])).content.decode()


def test_only_the_coordinator_can_delete_and_never_a_draft(as_user, user_of):
    complaint = submitted(as_user)
    for username in ("fa.pic", "fa.hod", "fa.fm", "sales", "management"):
        with pytest.raises(WorkflowError):
            workflow.delete(complaint, user_of(username), "no")
    lodge(as_user("sales"), save="1")
    draft = Complaint.objects.latest("id")
    assert draft.is_draft
    with pytest.raises(WorkflowError):
        workflow.delete(draft, user_of("mr"), "no")


# --- History, lists, access ---------------------------------------------------------------------------------------------


def test_the_history_records_every_step(as_user, user_of):
    complaint = replied(as_user, user_of)
    assert list(complaint.events.values_list("action", flat=True)) == [
        "submitted", "assigned", "sent_to_hod", "hod_to_fm", "fm_approved", "reply_sent",
    ]  # fmt: skip
    event = complaint.events.get(action="fm_approved")
    assert event.actor == user_of("fa.fm") and event.comment == "Good work"
    assert (event.from_status, event.to_status) == (S.FM_REVIEW, S.APPROVED)
    html = as_user("fa.fm").get(reverse("complaint_detail", args=[complaint.pk])).content.decode()
    assert "Approved by the FM" in html and "Good work" in html


def test_every_stage_page_renders_for_its_people(as_user, user_of):
    complaint = assigned(as_user, user_of, "fa.pic", "fa.lab", sample_sent="True")
    pic = user_of("fa.pic")
    write_findings(complaint, pic)
    ffa = LabTest.objects.get(name="FFA")
    workflow.save_lab(complaint, user_of("fa.lab"), datetime.date(2026, 11, 2), "", {ffa: ("1", "1")})
    issue(complaint, pic)
    returned = SimpleUploadedFile("back.pdf", b"%PDF-1.4")
    workflow.return_scar(fresh(complaint), pic, returned)
    workflow.submit_to_hod(fresh(complaint), pic)
    workflow.hod_submit_to_fm(fresh(complaint), user_of("fa.hod"), "valid", "external")
    workflow.fm_decide(fresh(complaint), user_of("fa.fm"), True)
    workflow.send_reply(fresh(complaint), pic, "s", "b")
    workflow.save_verification(fresh(complaint), user_of("mr"), datetime.date(2026, 12, 1), "ok", None, "")
    complaint = fresh(complaint)
    for username in ("mr", "fa.pic", "fa.lab", "fa.hod", "fa.fm", "management", "sales"):
        user = user_of(username)
        client = as_user(username)
        visible = Complaint.objects.visible_to(user).filter(pk=complaint.pk).exists()
        response = client.get(reverse("complaint_detail", args=[complaint.pk]))
        assert response.status_code == (200 if visible else 404), username
        if visible:
            assert "Laboratory section" in response.content.decode()
    for name in ("complaint_verify", "complaint_delete"):
        assert as_user("mr").get(reverse(name, args=[complaint.pk])).status_code == 200


def test_waiting_for_me_and_the_waiting_column(as_user, user_of):
    complaint = assigned(as_user, user_of)

    def waiting(username):
        return list(Complaint.objects.visible_to(user_of(username)).filter(workflow.waiting_for_me(user_of(username))))

    assert waiting("fa.pic") == [complaint] and waiting("mr") == [] and waiting("fa.hod") == []
    write_findings(complaint, user_of("fa.pic"))
    workflow.submit_to_hod(fresh(complaint), user_of("fa.pic"))
    complaint = fresh(complaint)
    assert waiting("fa.hod") == [complaint] and waiting("fa.pic") == []
    html = as_user("fa.hod").get(reverse("home")).content.decode()
    assert "<strong class=\"mine\">You</strong>" in html
    html = as_user("fa.pic").get(reverse("home")).content.decode()
    assert f"HOD - {user_of('fa.hod').get_full_name()}" in html  # the role and the person
    assert people.responsible_users(complaint) == [user_of("fa.hod")]
    only_mine = as_user("fa.hod").get(reverse("home"), {"mine": "on"}).content.decode()
    assert complaint.reference in only_mine
    assert complaint.reference not in as_user("fa.pic").get(reverse("home"), {"mine": "on"}).content.decode()


def test_other_factories_cannot_open_the_steps(as_user, user_of):
    complaint = at_hod(as_user, user_of)
    client = as_user("fb.hod")
    for name in ("complaint_assign", "complaint_investigation", "complaint_hod", "complaint_fm", "complaint_reply", "complaint_verify", "complaint_delete", "complaint_pdf"):
        assert client.get(reverse(name, args=[complaint.pk])).status_code == 404, name


def test_step_pages_need_a_post_to_change_anything(as_user, user_of):
    complaint = at_hod(as_user, user_of)
    client = as_user("fa.hod")
    client.get(reverse("complaint_hod", args=[complaint.pk]))
    assert fresh(complaint).status == S.HOD_REVIEW


def test_the_pdf_summary_is_a_pdf(as_user, user_of):
    complaint = submitted(as_user)
    response = as_user("mr").get(reverse("complaint_pdf", args=[complaint.pk]))
    assert response["Content-Type"] == "application/pdf" and response.content.startswith(b"%PDF")
    assert as_user("sales").get(reverse("complaint_pdf", args=[complaint.pk])).status_code == 200


def test_files_added_in_a_section_can_be_removed_by_their_uploader_while_it_is_open(as_user, user_of):
    complaint = assigned(as_user, user_of)
    pic = user_of("fa.pic")
    workflow.save_investigation(
        complaint, pic, "a", "b", "c", "internal", [SimpleUploadedFile("photo.png", b"png")]
    )
    attachment = Attachment.objects.get(section="investigation")
    assert not workflow.can_remove_attachment(user_of("fa.labmgr"), attachment)
    assert workflow.can_remove_attachment(pic, attachment)
    as_user("fa.labmgr").post(reverse("attachment_delete", args=[attachment.pk]))
    assert Attachment.objects.filter(pk=attachment.pk).exists()
    response = as_user("fa.pic").post(reverse("attachment_delete", args=[attachment.pk]))
    assert response.status_code == 302 and not Attachment.objects.filter(pk=attachment.pk).exists()
    # The lodger's files on a submitted complaint stay.
    lodging = Attachment.objects.create(
        complaint=complaint, file="x.png", original_name="x.png", size=1, uploaded_by=user_of("sales")
    )
    assert not workflow.can_remove_attachment(user_of("sales"), lodging)


def test_new_records_are_in_the_audit_log(as_user, user_of):
    from audit.models import AuditLog

    complaint = at_hod(as_user, user_of)
    labels = set(AuditLog.objects.values_list("content_type__model", flat=True))
    assert {"assignment", "investigation"} <= labels
    updated = AuditLog.objects.filter(content_type__model="complaint", object_id=str(complaint.pk), action="updated")
    assert any("status" in log.changes and log.changes["status"][1] == "hod_review" for log in updated)
