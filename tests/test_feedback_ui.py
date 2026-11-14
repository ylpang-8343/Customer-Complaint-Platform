"""Feedback of the demo: the lists, the master data upload, My Complaints, the dashboard, the export, the reply file, future dates."""

import datetime
import io
import re
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import Workbook, load_workbook

from accounts.models import Factory
from complaints import masterdata, people, reporting, workflow
from complaints.models import Complaint, ComplaintSubType, ComplaintType, Customer, PackSize, Product
from complaints.report_pdf import audit_pack, reply_pdf
from tests.test_complaints import as_user, complete_data, days_ago, lodge, make_draft  # noqa: F401
from tests.test_workflow import assigned, at_fm, at_hod, fresh, replied, submitted  # noqa: F401

def rows_of(html):
    body = html.split("<tbody>")[1].split("</tbody>")[0]
    return re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)


def references_on(html):
    return re.findall(r'<td><a href="/complaints/\d+/(?:edit/)?">([^<]+)</a></td>', html)


# --- The form: lists, received date, description, no future dates ---------------------------------------------


def test_the_form_offers_the_lists_and_the_new_labels(as_user):
    html = as_user("sales").get(reverse("complaint_new")).content.decode()
    for text in ("Buyer contact", "Complaint received date", "Example Buyer Ltd", "Sample Product", "Flexibag"):
        assert text in html
    assert "Contact at the buyer" not in html


@pytest.mark.parametrize("field", ["received_date", "loading_date", "discharge_date"])
def test_a_future_date_is_refused(as_user, field):
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    response = lodge(as_user("sales"), save="1", **{field: tomorrow})
    assert response.status_code == 200 and "cannot be in the future" in response.content.decode()
    assert not Complaint.objects.exists()


def test_the_date_pickers_stop_at_today(as_user):
    html = as_user("sales").get(reverse("complaint_new")).content.decode()
    assert f'max="{datetime.date.today().isoformat()}"' in html


def test_the_description_is_made_of_its_five_parts(as_user):
    lodge(
        as_user("sales"), save="1", issue_identified="Leak", customer_findings="Wet cartons",
        customer_test_result="FFA 0.5", customer_request="Credit note", description_others="Photos attached",
    )
    text = Complaint.objects.get().description
    for part in ("Leak", "Wet cartons", "FFA 0.5", "Credit note", "Photos attached"):
        assert part in text


def test_the_detail_page_shows_the_received_date_and_the_buyer_contact(as_user):
    complaint = submitted(as_user)
    html = as_user("mr").get(reverse("complaint_detail", args=[complaint.pk])).content.decode()
    assert "Buyer contact" in html and "Complaint received date" in html


# --- Master data upload ---------------------------------------------------------------------------------------------


def workbook_file(rows, name="list.xlsx"):
    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return SimpleUploadedFile(name, buffer.getvalue())


def test_only_the_coordinator_reaches_the_master_data(as_user):
    assert as_user("mr").get(reverse("masterdata_home")).status_code == 200
    for username in ("sales", "fa.pic", "management"):
        assert as_user(username).get(reverse("masterdata_home")).status_code == 302
        assert as_user(username).post(reverse("masterdata_upload", args=["customers"]), {}).status_code == 302


def test_the_templates_download_for_each_list(as_user):
    client = as_user("mr")
    for slug, header in (("customers", "Customer code"), ("products", "Product code / SKU"), ("pack-sizes", "Pack size")):
        response = client.get(reverse("masterdata_template", args=[slug]))
        sheet = load_workbook(io.BytesIO(response.content)).worksheets[0]
        assert sheet["A1"].value == header
    assert as_user("mr").get(reverse("masterdata_template", args=["nothing"])).status_code == 404


def test_uploading_customers_adds_and_updates_and_never_deletes(as_user):
    before = Customer.objects.count()
    file = workbook_file([["Customer code", "Customer name", "Active"], ["C0001", "Example Buyer Renamed", "Yes"], ["C9001", "Brand New Buyer", ""], ["C9002", "Old Buyer", "No"]])
    response = as_user("mr").post(reverse("masterdata_upload", args=["customers"]), {"file": file}, follow=True)
    assert "2 added, 1 updated" in response.content.decode()
    assert Customer.objects.count() == before + 2
    assert Customer.objects.get(code="C0001").name == "Example Buyer Renamed"
    assert Customer.objects.get(code="C9002").is_active is False


def test_a_bad_upload_saves_nothing_and_lists_the_rows(as_user):
    before = Product.objects.count()
    file = workbook_file([["Product code / SKU", "Product name", "Active"], ["P9001", "Good", "Yes"], ["", "No code", "Yes"], ["P9001", "Twice", "Yes"], ["P9003", "Odd", "maybe"]])
    html = as_user("mr").post(reverse("masterdata_upload", args=["products"]), {"file": file}).content.decode()
    assert "needed" in html and "appears twice" in html and "not Yes or No" in html
    assert Product.objects.count() == before


def test_a_csv_with_the_wrong_headers_is_refused(as_user):
    file = SimpleUploadedFile("sizes.csv", b"Size,Where\nDrum,here\n")
    html = as_user("mr").post(reverse("masterdata_upload", args=["pack-sizes"]), {"file": file}).content.decode()
    assert "Missing: Pack size" in html and not PackSize.objects.filter(name="Barrel 99").exists()


def test_a_csv_upload_works_and_the_new_entry_is_offered_on_the_form(as_user):
    file = SimpleUploadedFile("sizes.csv", b"Pack size,Active\nBulk tanker 25 t,Yes\n")
    as_user("mr").post(reverse("masterdata_upload", args=["pack-sizes"]), {"file": file})
    assert "Bulk tanker 25 t" in as_user("sales").get(reverse("complaint_new")).content.decode()


def test_an_inactive_entry_is_not_offered_but_old_complaints_keep_it(as_user):
    complaint = submitted(as_user)
    Customer.objects.filter(pk=complaint.customer_id).update(is_active=False)
    assert "Example Buyer Ltd" not in as_user("sales").get(reverse("complaint_new")).content.decode()
    assert fresh(complaint).buyer == "Example Buyer Ltd"


# --- My complaints: page size, columns, who it waits for, filters ---------------------------------------------------


@pytest.fixture
def many(as_user, user_of):
    """Twelve drafts of the sales user (so one person sees all twelve)."""
    sales = user_of("sales")
    for _ in range(12):
        make_draft(sales)
    return as_user("sales")


def test_my_complaints_shows_ten_at_a_time_and_the_total(many):
    html = many.get(reverse("home")).content.decode()
    assert len(references_on(html)) == 10
    assert "Page 1 of 2 (12 complaints)" in html


def test_the_page_size_can_be_changed(many):
    assert len(references_on(many.get(reverse("home"), {"page_size": "25"}).content.decode())) == 12
    second = many.get(reverse("home"), {"page_size": "10", "page": 2}).content.decode()
    assert len(references_on(second)) == 2 and "page_size=10" in second


def test_my_complaints_has_the_columns_of_the_feedback(many):
    html = many.get(reverse("home")).content.decode()
    heads = re.findall(r"<th[^>]*>([^<]*)</th>", html.split("<thead>")[1].split("</thead>")[0])
    assert heads == ["Reference", "Factory", "Buyer", "Product", "Type", "Status", "Waiting for", "Days Open"]


def test_it_names_the_person_each_complaint_is_waiting_for(as_user, user_of):
    complaint = assigned(as_user, user_of, "fa.pic")
    html = as_user("mr").get(reverse("home")).content.decode()
    assert "PIC - " in html and user_of("fa.pic").get_full_name() in html
    assert people.waiting_text(complaint).startswith("PIC - ")
    at_review = at_hod(as_user, user_of)
    assert people.waiting_text(at_review).startswith("HOD - ")
    assert people.waiting_text(submitted(as_user)).startswith("Coordinator - ")


def test_days_open_counts_from_the_received_date(as_user, user_of):
    complaint = submitted(as_user, received_date=days_ago(9))
    assert fresh(complaint).days_open == 9
    html = as_user("mr").get(reverse("home")).content.decode()
    assert re.search(r"<td>9</td>", html)
    draft = make_draft(user_of("sales"))
    assert draft.days_open is None


def test_my_complaints_filters_by_product_buyer_and_category(as_user):
    a = submitted(as_user, product_name="RBD Palm Olein", buyer="Buyer One")
    b = submitted(as_user, product_name="PFAD", buyer="Buyer Two", complaint_type=ComplaintType.objects.get(name="Logistic").pk, complaint_sub_type=ComplaintSubType.objects.get(name="Logistic - Shortage").pk)
    mr = as_user("mr")
    home = reverse("home")
    assert references_on(mr.get(home, {"product": a.product_id}).content.decode()) == [fresh(a).reference]
    assert references_on(mr.get(home, {"customer": b.customer_id}).content.decode()) == [fresh(b).reference]
    assert references_on(mr.get(home, {"complaint_type": b.complaint_type_id}).content.decode()) == [fresh(b).reference]


# --- Dashboard, log and export --------------------------------------------------------------------------------------


@pytest.fixture
def portfolio(as_user, user_of):
    mr = user_of("mr")
    a = submitted(as_user, product_name="RBD Palm Olein", buyer="Buyer One")
    b = submitted(as_user, factory=Factory.objects.get(code="FB").pk, product_name="PFAD", buyer="Buyer Two")
    invalid = at_fm(as_user, user_of, decision="invalid")
    deleted = submitted(as_user)
    workflow.delete(deleted, mr, "Entered twice by mistake. " + "Very long explanation. " * 10)
    return {"a": fresh(a), "b": fresh(b), "invalid": fresh(invalid), "deleted": fresh(deleted)}


def test_the_dashboard_counts_the_complaints_of_each_factory(portfolio, user_of):
    pairs = dict(reporting.by_factory(reporting.scoped(user_of("mr"))))
    assert pairs["Factory A"] == 3 and pairs["Factory B"] == 1


def test_the_dashboard_chart_by_factory_is_on_the_page(portfolio, as_user):
    html = as_user("mr").get(reverse("dashboard")).content.decode()
    assert "Complaints by factory" in html and "Factory B" in html


def test_a_reason_is_shortened_and_links_to_the_complaint(portfolio, as_user):
    html = as_user("mr").get(reverse("dashboard")).content.decode()
    section = html.split("Invalid and deleted complaints")[1]
    assert "Reason (summary)" in section and "…" in section
    assert f'href="/complaints/{portfolio["deleted"].pk}/"' in section and "Very long explanation. " * 6 not in section


def test_the_open_complaints_are_paged_without_a_cap(as_user, user_of):
    for _ in range(12):
        submitted(as_user)
    client = as_user("mr")
    first = client.get(reverse("dashboard")).content.decode()
    assert "Page 1 of 2 (12 items)" in first
    assert len(rows_of(first.split("Where each open complaint is waiting")[1])) == 10
    second = client.get(reverse("dashboard"), {"open_page": 2}).content.decode()
    assert "Page 2 of 2" in second
    assert "Page 1 of 1 (12 items)" in client.get(reverse("dashboard"), {"page_size": "25"}).content.decode()


def test_the_log_is_filtered_by_product_buyer_category_status_and_result(portfolio, as_user):
    mr, log = as_user("mr"), reverse("complaint_log")

    def listed(**params):
        return len(re.findall(r'<td><a href="/complaints/\d+/">', mr.get(log, {"page_size": "100", **params}).content.decode()))

    assert listed() == 4
    assert listed(product=portfolio["a"].product_id) == 1
    assert listed(customer=portfolio["b"].customer_id) == 1
    assert listed(status="deleted") == 1
    assert listed(decision="invalid") == 1
    assert listed(decision="undecided") == 2
    assert listed(q=portfolio["b"].reference) == 1
    assert listed(complaint_type=portfolio["a"].complaint_type_id) >= 1


def test_the_export_follows_the_filters_of_the_screen(portfolio, as_user):
    mr = as_user("mr")
    everything = load_workbook(io.BytesIO(mr.get(reverse("log_export")).content)).worksheets[0]
    only_deleted = load_workbook(io.BytesIO(mr.get(reverse("log_export"), {"status": "deleted"}).content)).worksheets[0]
    only_b = load_workbook(io.BytesIO(mr.get(reverse("log_export"), {"factories": Factory.objects.get(code="FB").pk}).content)).worksheets[0]

    def references(sheet):
        return {cell.value for cell in sheet["B"][1:] if cell.value and re.match(r"F[A-E]/\d{4}/\d+", str(cell.value))}

    assert len(references(everything)) == 4
    assert references(only_deleted) == {portfolio["deleted"].reference}
    assert references(only_b) == {portfolio["b"].reference}
    assert reverse("log_export") in mr.get(reverse("dashboard"), {"status": "deleted"}).content.decode()
    assert "status=deleted" in mr.get(reverse("dashboard"), {"status": "deleted"}).content.decode()


# --- The reply as a file --------------------------------------------------------------------------------------------


def test_the_detail_page_offers_the_reply_as_a_file_not_the_whole_email(as_user, user_of):
    complaint = replied(as_user, user_of)
    html = as_user("mr").get(reverse("complaint_detail", args=[complaint.pk])).content.decode()
    assert reverse("complaint_reply_pdf", args=[complaint.pk]) in html and "Reply to customer" in html
    assert "Dear customer, sorry." not in html
    assert "Dear customer, sorry." not in as_user("mr").get(reverse("complaint_verify", args=[complaint.pk])).content.decode()


def test_the_reply_pdf_downloads_for_people_who_can_see_the_complaint(as_user, user_of):
    complaint = replied(as_user, user_of)
    response = as_user("mr").get(reverse("complaint_reply_pdf", args=[complaint.pk]))
    assert response.status_code == 200 and response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"].startswith("attachment") and response.content.startswith(b"%PDF")
    assert as_user("fb.pic").get(reverse("complaint_reply_pdf", args=[complaint.pk])).status_code == 404  # another factory
    assert as_user("mr").get(reverse("complaint_reply_pdf", args=[submitted(as_user).pk])).status_code == 404  # no reply yet


def test_the_reply_pdf_holds_the_email_and_goes_into_the_audit_pack(as_user, user_of):
    from pypdf import PdfReader

    complaint = replied(as_user, user_of)
    text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(reply_pdf(complaint))).pages)
    assert "Dear customer, sorry." in text and "Re: your complaint" in text and complaint.reference in text
    names = zipfile.ZipFile(io.BytesIO(audit_pack(complaint, user_of("mr")))).namelist()
    assert any(name.endswith(".pdf") and "/reply/" in name for name in names)


# --- No future dates on the later steps -----------------------------------------------------------------------------


def test_the_verified_dates_cannot_be_in_the_future(as_user, user_of):
    complaint = replied(as_user, user_of)
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    client, url = as_user("mr"), reverse("complaint_verify", args=[complaint.pk])
    for field in ("first_verified_date", "second_verified_date"):
        response = client.post(url, {"save": "1", "first_verified_date": days_ago(5), "first_result": "ok", field: tomorrow})
        assert "cannot be in the future" in response.content.decode()
    assert not hasattr(fresh(complaint), "verification")
    assert f'max="{datetime.date.today().isoformat()}"' in client.get(url).content.decode()


def test_the_lab_acknowledgement_date_cannot_be_in_the_future(as_user, user_of):
    complaint = assigned(as_user, user_of, "fa.pic", "fa.lab", sample_sent="True")
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    response = as_user("fa.lab").post(reverse("complaint_lab", args=[complaint.pk]), {"acknowledged_on": tomorrow})
    assert "cannot be in the future" in response.content.decode()
    assert not hasattr(fresh(complaint), "lab") or fresh(complaint).lab.acknowledged_on is None
