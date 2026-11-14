"""Step 6, part 1: the PDF report and the audit pack."""

import datetime
import io
import zipfile

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from pypdf import PdfReader

from complaints import workflow
from complaints.models import Attachment, Complaint, LabTest
from complaints.report_pdf import audit_pack, complaint_report_pdf, safe
from tests.test_complaints import as_user, lodge  # noqa: F401  (fixtures and helpers)
from tests.test_workflow import assigned, fresh, issue, submitted, write_findings  # noqa: F401


def pdf_text(data):
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() for page in reader.pages), len(reader.pages)


def full_cycle(as_user, user_of, **overrides):
    """A complaint that went through every step: sample, lab, SCAR, a rejection, reply and closing."""
    complaint = assigned(
        as_user, user_of, "fa.pic", "fa.lab", sample_sent="True",
        attachments=SimpleUploadedFile("leak-photo.png", b"png-bytes"), **overrides,
    )  # fmt: skip
    pic, hod, fm, mr = user_of("fa.pic"), user_of("fa.hod"), user_of("fa.fm"), user_of("mr")
    workflow.save_investigation(
        complaint, pic, "Seal failed at the weld", "Replaced the flexibags", "Audit the supplier", "external",
        [SimpleUploadedFile("photo.png", b"png-bytes")],
    )  # fmt: skip
    ffa = LabTest.objects.get(name="FFA")
    workflow.save_lab(
        fresh(complaint), user_of("fa.lab"), datetime.date(2026, 11, 2), "Within limits",
        {ffa: ("0.42%", "0.44%")}, [SimpleUploadedFile("lab-report.pdf", b"%PDF-1.4 lab")],
    )  # fmt: skip
    issue(fresh(complaint), pic)
    workflow.return_scar(fresh(complaint), pic, SimpleUploadedFile("returned.pdf", b"%PDF-1.4 scar"))
    workflow.submit_to_hod(fresh(complaint), pic)
    workflow.hod_submit_to_fm(fresh(complaint), hod, "valid", "external")
    workflow.fm_decide(fresh(complaint), fm, False, "Give the audit date")
    workflow.submit_to_hod(fresh(complaint), pic)
    workflow.hod_submit_to_fm(fresh(complaint), hod, "valid", "external")
    workflow.fm_decide(fresh(complaint), fm, True, "Approved with thanks")
    workflow.send_reply(fresh(complaint), pic, "Your complaint", "Dear customer, we are sorry about the leak.")
    workflow.save_verification(fresh(complaint), mr, datetime.date(2026, 12, 1), "No repeat", None, "")
    workflow.close_case(fresh(complaint), mr, "Effective")
    return fresh(complaint)


def test_the_report_has_every_section_of_a_complaint_that_went_through_the_process(as_user, user_of):
    complaint = full_cycle(as_user, user_of)
    text, pages = pdf_text(complaint_report_pdf(complaint, user_of("mr")))
    for heading in (
        "Customer Complaint Report", complaint.reference, "LODGED", "COMPLAINT", "TRANSPORT", "DESCRIPTION",
        "BUYER CONTACT", "COORDINATOR SECTION (IMS)", "INVESTIGATION AND RESPONSE", "SCAR TO AN EXTERNAL PARTY",
        "LABORATORY SECTION", "HOD DECISION", "REVIEW AND APPROVAL RECORDS", "REPLY TO THE CUSTOMER",
        "EFFECTIVENESS AND CLOSING", "DOCUMENTS", "HISTORY",
    ):  # fmt: skip
        assert heading in text, heading
    for content in (
        "Seal failed at the weld", "Replaced the flexibags", "Audit the supplier", "Seal Makers Ltd",
        "Within limits", "0.42%", "0.44%", "Give the audit date", "Approved with thanks", "Dear customer",
        "1 Dec 2026", "No repeat", "Effective", "photo.png", "lab-report.pdf", "returned.pdf",
    ):  # fmt: skip
        assert content in text, content
    assert "Rejected by the FM" in text and "Case closed" in text and "Reply sent to the customer" in text
    assert pages >= 3
    assert f"Page 1 of {pages}" in text and f"Page {pages} of {pages}" in text
    assert "Printed" in text and "by Sample Coordinator" in text


def test_the_laboratory_section_appears_only_when_a_sample_was_sent(as_user, user_of):
    without = assigned(as_user, user_of)  # sample_sent is No
    text, _ = pdf_text(complaint_report_pdf(without))
    assert "LABORATORY SECTION" not in text
    with_sample = assigned(as_user, user_of, "fa.pic", "fa.lab", sample_sent="True")
    text, _ = pdf_text(complaint_report_pdf(with_sample))
    assert "LABORATORY SECTION" in text


def test_blank_fields_are_shown_as_a_dash_not_left_out(as_user, user_of):
    complaint = submitted(as_user)
    text, _ = pdf_text(complaint_report_pdf(complaint))
    assert "COORDINATOR SECTION (IMS)" in text and "Received by" in text and "Date case closed" in text


def test_a_deleted_complaint_says_so_with_its_reason(as_user, user_of):
    complaint = assigned(as_user, user_of)
    workflow.delete(complaint, user_of("mr"), "Duplicate of FA/2026/0009")
    text, _ = pdf_text(complaint_report_pdf(fresh(complaint)))
    assert "DELETED on" in text and "Duplicate of FA/2026/0009" in text and "Deleted" in text


def test_characters_the_pdf_fonts_cannot_draw_become_question_marks(as_user, user_of):
    assert safe("Café 上海 Foods — ok") == "Café ?? Foods — ok"
    complaint = submitted(as_user, buyer="上海 Foods Sdn Bhd")
    text, _ = pdf_text(complaint_report_pdf(complaint))
    assert "?? Foods Sdn Bhd" in text


def test_a_very_long_text_runs_over_several_pages_without_failing(as_user, user_of):
    complaint = assigned(as_user, user_of)
    long_text = ("The seal failed because of a weld defect. " * 40 + "\n") * 25
    workflow.save_investigation(complaint, user_of("fa.pic"), long_text, "x", "y", "internal")
    text, pages = pdf_text(complaint_report_pdf(fresh(complaint)))
    assert pages >= 3 and "weld defect" in text


def test_a_draft_has_a_report_for_its_lodger_only(as_user, user_of):
    lodge(as_user("sales"), save="1")
    draft = Complaint.objects.get()
    response = as_user("sales").get(reverse("complaint_pdf", args=[draft.pk]))
    assert response.status_code == 200 and "Draft" in pdf_text(response.content)[0]
    assert as_user("mr").get(reverse("complaint_pdf", args=[draft.pk])).status_code == 404


def test_the_report_download(as_user, user_of):
    complaint = submitted(as_user)
    response = as_user("mr").get(reverse("complaint_pdf", args=[complaint.pk]))
    assert response["Content-Type"] == "application/pdf" and response.content.startswith(b"%PDF")
    name = complaint.reference.replace("/", "-")
    assert response["Content-Disposition"] == f'attachment; filename="{name}.pdf"'
    assert "by Sample Coordinator" in pdf_text(response.content)[0]
    assert as_user("fb.pic").get(reverse("complaint_pdf", args=[complaint.pk])).status_code == 404


def test_the_page_offers_the_report_and_the_audit_pack(as_user, user_of):
    complaint = submitted(as_user)
    html = as_user("mr").get(reverse("complaint_detail", args=[complaint.pk])).content.decode()
    assert "PDF report" in html and "Audit pack" in html and "PDF summary" not in html


# --- The audit pack -------------------------------------------------------------------------------------------------


def names_in(data):
    return sorted(zipfile.ZipFile(io.BytesIO(data)).namelist())


def test_the_audit_pack_holds_the_report_the_scar_the_reply_and_every_file(as_user, user_of):
    complaint = full_cycle(as_user, user_of)
    ref = complaint.reference.replace("/", "-")
    data = audit_pack(complaint, user_of("mr"))
    names = names_in(data)
    assert f"{ref}/{ref}-report.pdf" in names
    assert any(n.startswith(f"{ref}/scar/SCAR-{ref}") and n.endswith(".pdf") for n in names)
    assert f"{ref}/reply/Reply-{ref}.pdf" in names  # the reply is a document, like the report
    for expected in ("complaint-form/leak-photo.png", "investigation/photo.png", "lab/lab-report.pdf", "scar/returned.pdf"):
        assert f"{ref}/documents/{expected}" in names, expected
    archive = zipfile.ZipFile(io.BytesIO(data))
    assert archive.read(f"{ref}/documents/investigation/photo.png") == b"png-bytes"
    assert "we are sorry about the leak" in pdf_text(archive.read(f"{ref}/reply/Reply-{ref}.pdf"))[0]
    assert pdf_text(archive.read(f"{ref}/{ref}-report.pdf"))[0].count(complaint.reference) >= 2


def test_two_files_with_the_same_name_are_both_kept(as_user, user_of):
    complaint = assigned(as_user, user_of)
    pic = user_of("fa.pic")
    for content in (b"first", b"second"):
        workflow.save_investigation(complaint, pic, "a", "b", "c", "internal", [SimpleUploadedFile("photo.png", content)])
    ref = complaint.reference.replace("/", "-")
    archive = zipfile.ZipFile(io.BytesIO(audit_pack(fresh(complaint))))
    assert archive.read(f"{ref}/documents/investigation/photo.png") == b"first"
    assert archive.read(f"{ref}/documents/investigation/photo (2).png") == b"second"


def test_a_file_that_is_no_longer_in_storage_is_listed_not_fatal(as_user, user_of):
    complaint = submitted(as_user, attachments=SimpleUploadedFile("leak-photo.png", b"png-bytes"))
    attachment = Attachment.objects.get(complaint=complaint)
    attachment.file.storage.delete(attachment.file.name)
    ref = complaint.reference.replace("/", "-")
    archive = zipfile.ZipFile(io.BytesIO(audit_pack(fresh(complaint))))
    assert f"{ref}/MISSING-FILES.txt" in archive.namelist()
    assert "leak" not in " ".join(n for n in archive.namelist() if "documents" in n)
    assert b"no longer in storage" in archive.read(f"{ref}/MISSING-FILES.txt")


def test_the_audit_pack_download_and_who_may_get_it(as_user, user_of):
    complaint = submitted(as_user)
    ref = complaint.reference.replace("/", "-")
    response = as_user("mr").get(reverse("complaint_audit_pack", args=[complaint.pk]))
    assert response["Content-Type"] == "application/zip"
    assert response["Content-Disposition"] == f'attachment; filename="{ref}-audit-pack.zip"'
    assert zipfile.ZipFile(io.BytesIO(response.content)).testzip() is None
    assert as_user("management").get(reverse("complaint_audit_pack", args=[complaint.pk])).status_code == 200
    assert as_user("fb.pic").get(reverse("complaint_audit_pack", args=[complaint.pk])).status_code == 404
    lodge(as_user("sales"), save="1")
    draft = Complaint.objects.latest("id")
    back = as_user("sales").get(reverse("complaint_audit_pack", args=[draft.pk]))
    assert back.status_code == 302 and "audit-pack" not in back["Location"]
