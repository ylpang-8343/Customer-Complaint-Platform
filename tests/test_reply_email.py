"""The reply to the customer follows the template in docs/emailTemplate.md, and the investigation records what it needs."""

import io

from django.urls import reverse
from pypdf import PdfReader

from accounts.models import FactoryPIC
from complaints import texts, workflow
from complaints.models import Complaint
from tests.test_complaints import as_user, days_ago, lodge  # noqa: F401  (fixtures and helpers)
from tests.test_workflow import assigned, fresh, submitted, write_findings  # noqa: F401

EN_DASH = "–"


def days_ago_text(days):
    import datetime

    from django.utils import formats

    return formats.date_format(datetime.date.today() - datetime.timedelta(days=days), "j M Y")


def approved_with(as_user, user_of, summary="", conclusion="", root_cause="Seal failed", correction="Replaced the pack", action="Audit the supplier", **overrides):
    """A complaint the FM approved, with the findings given."""
    complaint = assigned(as_user, user_of, **overrides)
    pic, hod, fm = user_of("fa.pic"), user_of("fa.hod"), user_of("fa.fm")
    workflow.save_investigation(
        complaint, pic, root_cause, correction, action, "internal", investigation_summary=summary, conclusion=conclusion
    )  # fmt: skip
    workflow.submit_to_hod(fresh(complaint), pic)
    workflow.hod_submit_to_fm(fresh(complaint), hod, "valid", "internal")
    workflow.fm_decide(fresh(complaint), fm, True, "Approved")
    return fresh(complaint)


# --- The subject --------------------------------------------------------------------------------------------------


def test_the_subject_names_the_complaint_the_product_and_the_contract():
    complaint = Complaint(reference="FA/2026/0005", product_name="RPO FM PG", contract_number="SD123456")
    assert texts.reply_subject(complaint) == f"Investigation Findings for Complaint FA/2026/0005 {EN_DASH} RPO FM PG / SD123456"


def test_the_subject_leaves_out_what_the_complaint_does_not_have():
    assert texts.reply_subject(Complaint(reference="FA/2026/0005", product_name="RPO")) == f"Investigation Findings for Complaint FA/2026/0005 {EN_DASH} RPO"
    assert texts.reply_subject(Complaint(reference="FA/2026/0005")) == "Investigation Findings for Complaint FA/2026/0005"


# --- The body ----------------------------------------------------------------------------------------------------------


def test_the_body_has_the_parts_of_the_template_in_order(as_user, user_of):
    complaint = approved_with(
        as_user, user_of, summary="We reviewed the quality records and the retained sample.",
        conclusion="The product met specification at loading.", contact_person="Alice Tan",
    )  # fmt: skip
    body = texts.reply_message(complaint, user_of("fa.pic"))
    expected = [
        "Dear Alice Tan,",
        "Thank you for notifying us regarding the quality concern for the shipment detailed below:",
        "   - Product: Sample Product",
        "   - Quantity: 20 Metric Ton",
        "   - Vessel: MV Example",
        "   - Container: ABCD1234567",
        f"   - Loading Date: {days_ago_text(20)}",
        f"   - Discharge Date: {days_ago_text(6)}",
        "   - Loading Port: Example Loading Port",
        "   - Port of Discharge: Example Discharge Port",
        "   - Contract No.: SC-000123",
        "   - Complaint: Flexibag leaked during discharge.",
        "Following our investigation, we would like to share the following findings:",
        "Investigation Summary",
        "We reviewed the quality records and the retained sample.",
        "Root Cause Assessment",
        "Seal failed",
        "Conclusion",
        "The product met specification at loading.",
        "Corrective and Preventive Actions",
        "Audit the supplier",
        "We appreciate your cooperation throughout this investigation.",
        "Thank you for your continued support.",
        "Yours sincerely,",
        "Sample FM FA",
        "Factory Manager, Factory A",
        "BEST Group",
    ]  # fmt: skip
    position = -1
    for line in expected:
        found = body.find(line, position + 1)
        assert found > position, f"{line!r} is missing or out of order"
        position = found
    assert body.endswith("Sample FM FA\nFactory Manager, Factory A\nBEST Group")


def test_the_whole_body_reads_as_the_template(as_user, user_of):
    complaint = approved_with(as_user, user_of, summary="Records reviewed.", conclusion="Met specification.", contact_person="Alice Tan")
    assert texts.reply_message(complaint, user_of("fa.pic")) == (
        "Dear Alice Tan,\n\n"
        "Thank you for notifying us regarding the quality concern for the shipment detailed below:\n\n"
        "   - Product: Sample Product\n   - Quantity: 20 Metric Ton\n   - Vessel: MV Example\n   - Container: ABCD1234567\n"
        f"   - Loading Date: {days_ago_text(20)}\n   - Discharge Date: {days_ago_text(6)}\n   - Loading Port: Example Loading Port\n"
        "   - Port of Discharge: Example Discharge Port\n   - Contract No.: SC-000123\n"
        "   - Complaint: Flexibag leaked during discharge.\n\n"
        "Following our investigation, we would like to share the following findings:\n\n"
        "Investigation Summary\nRecords reviewed.\n\n"
        "Root Cause Assessment\nSeal failed\n\n"
        "Conclusion\nMet specification.\n\n"
        "Corrective and Preventive Actions\nAudit the supplier\n\n"
        "We appreciate your cooperation throughout this investigation. Should you require any further clarification or wish "
        "to discuss the findings in more detail, please do not hesitate to contact us.\n\n"
        "Thank you for your continued support.\n\n"
        "Yours sincerely,\n\n"
        "Sample FM FA\nFactory Manager, Factory A\nBEST Group"
    )  # fmt: skip


def test_a_section_with_nothing_in_it_is_left_out(as_user, user_of):
    complaint = approved_with(as_user, user_of, summary="", conclusion="")  # only root cause and action were written
    body = texts.reply_message(complaint, user_of("fa.pic"))
    assert "Investigation Summary" not in body and "Conclusion" not in body
    assert "Root Cause Assessment\nSeal failed" in body and "Corrective and Preventive Actions\nAudit the supplier" in body


def test_a_local_delivery_lists_the_vehicle_not_a_vessel(as_user, user_of):
    complaint = approved_with(as_user, user_of, transport_type="local_bulk", vehicle_number="ABC 1234", delivery_date=days_ago(8))
    body = texts.reply_message(complaint, user_of("fa.pic"))
    assert "   - Vehicle: ABC 1234" in body and "Vessel:" not in body and "Container:" not in body
    # A local delivery has one delivery date, a place of loading and a destination (not a loading and a discharge date).
    assert f"   - Delivery Date: {days_ago_text(8)}" in body and "Loading Date:" not in body and "Discharge Date:" not in body
    assert "   - Place of Loading: Example Loading Port" in body and "   - Destination: Example Discharge Port" in body


def test_the_customer_is_greeted_by_contact_then_by_the_buyers_team(as_user, user_of):
    assert texts.reply_message(approved_with(as_user, user_of, contact_person="Alice Tan"), None).startswith("Dear Alice Tan,\n")
    assert texts.reply_message(approved_with(as_user, user_of), None).startswith("Dear Example Buyer Ltd Team,\n")
    assert texts._customer_name(Complaint(buyer="")) == "Sir/Madam"


def test_the_complaint_line_is_the_description_on_one_line_and_not_too_long():
    short = Complaint(description="Line one.\n\n  Line   two.")
    assert texts._complaint_summary(short) == "Line one. Line two."
    long = texts._complaint_summary(Complaint(description="word " * 400))
    assert long.endswith(" ...") and len(long) <= texts.COMPLAINT_SUMMARY_LIMIT + 4
    assert texts._complaint_summary(Complaint(description="")) == ""


def test_the_signature_is_the_fm_who_approved_or_else_the_sender(as_user, user_of):
    complaint = approved_with(as_user, user_of)
    assert texts._signature(complaint, user_of("fa.pic")) == ["Sample FM FA", "Factory Manager, Factory A", "BEST Group"]
    unapproved = submitted(as_user)  # nobody has approved it: the person sending signs
    assert texts._signature(unapproved, user_of("fa.pic")) == ["Sample PIC FA", "PIC, Factory A", "BEST Group"]
    assert texts._signature(unapproved, None) == ["Factory A", "BEST Group"]


def test_the_company_name_can_be_set(as_user, user_of, settings):
    settings.COMPANY_NAME = "Example Oils Sdn Bhd"
    assert texts.reply_message(approved_with(as_user, user_of), None).endswith("Factory Manager, Factory A\nExample Oils Sdn Bhd")


def test_the_reply_page_offers_the_template_to_edit_and_the_edited_text_is_what_is_sent(as_user, user_of):
    from django.core import mail

    complaint = approved_with(as_user, user_of, summary="Records reviewed.", contact_person="Alice Tan")
    client = as_user("fa.pic")
    url = reverse("complaint_reply", args=[complaint.pk])
    html = client.get(url).content.decode()
    assert f"Investigation Findings for Complaint {complaint.reference} {EN_DASH} Sample Product / SC-000123" in html
    assert "Dear Alice Tan," in html and "Investigation Summary" in html and "Records reviewed." in html
    mail.outbox.clear()
    client.post(url, {"subject": "Edited subject", "body": "Dear Alice,\n\nOur edited findings."})
    reply = fresh(complaint).reply
    assert (reply.subject, reply.body) == ("Edited subject", "Dear Alice,\n\nOur edited findings.")
    assert mail.outbox[0].subject == "Edited subject" and mail.outbox[0].body == "Dear Alice,\n\nOur edited findings."


# --- The two new fields of the investigation ---------------------------------------------------------------------------


def test_the_pic_enters_the_summary_and_the_conclusion(as_user, user_of):
    complaint = assigned(as_user, user_of)
    client = as_user("fa.pic")
    url = reverse("complaint_investigation", args=[complaint.pk])
    html = client.get(url).content.decode()
    assert 'name="investigation_summary"' in html and 'name="conclusion"' in html
    client.post(url, {"save": "1", "investigation_summary": "Records reviewed", "root_cause": "Weld", "conclusion": "Met specification",
                      "correction": "x", "corrective_action": "y", "cause": "internal"})  # fmt: skip
    investigation = fresh(complaint).investigation
    assert (investigation.investigation_summary, investigation.conclusion) == ("Records reviewed", "Met specification")
    assert "Records reviewed" in client.get(url).content.decode()  # shown again when the page is reopened


def test_older_callers_do_not_blank_the_new_fields(as_user, user_of):
    complaint = assigned(as_user, user_of)
    pic = user_of("fa.pic")
    workflow.save_investigation(complaint, pic, "a", "b", "c", "internal", investigation_summary="Kept", conclusion="Kept too")
    workflow.save_investigation(fresh(complaint), pic, "a2", "b2", "c2", "internal")  # does not mention them
    investigation = fresh(complaint).investigation
    assert (investigation.investigation_summary, investigation.conclusion, investigation.root_cause) == ("Kept", "Kept too", "a2")


def test_the_reviewers_the_page_and_the_pdf_show_them(as_user, user_of):
    complaint = approved_with(as_user, user_of, summary="Records reviewed.", conclusion="Met specification.")
    for username, name in (("fa.pic", "complaint_detail"), ("mr", "complaint_detail"), ("fa.fm", "complaint_detail")):
        html = as_user(username).get(reverse(name, args=[complaint.pk])).content.decode()
        assert "Investigation summary" in html and "Records reviewed." in html and "Met specification." in html
    pdf = as_user("mr").get(reverse("complaint_pdf", args=[complaint.pk])).content
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
    assert "Investigation summary" in text and "Records reviewed." in text and "Conclusion" in text and "Met specification." in text


def test_the_hod_and_the_fm_see_them_on_their_review_pages(as_user, user_of):
    complaint = assigned(as_user, user_of)
    pic = user_of("fa.pic")
    workflow.save_investigation(complaint, pic, "Weld", "Fix", "Prevent", "internal", investigation_summary="Records reviewed", conclusion="Met specification")
    workflow.submit_to_hod(fresh(complaint), pic)
    html = as_user("fa.hod").get(reverse("complaint_hod", args=[complaint.pk])).content.decode()
    assert "Records reviewed" in html and "Met specification" in html
    workflow.hod_submit_to_fm(fresh(complaint), user_of("fa.hod"), "valid", "internal")
    html = as_user("fa.fm").get(reverse("complaint_fm", args=[complaint.pk])).content.decode()
    assert "Records reviewed" in html and "Met specification" in html
