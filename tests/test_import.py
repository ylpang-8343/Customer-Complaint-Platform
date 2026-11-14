"""Step 6, part 2: the upload of past complaints."""

import datetime
import io
import re

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook, load_workbook

from complaints import importer, reminders, reporting, workflow
from complaints.models import Complaint, ComplaintEvent, ImportBatch, NotificationLog
from tests.test_complaints import as_user, lodge  # noqa: F401  (fixtures and helpers)

HEADERS = [c.header for c in importer.COLUMNS]
TODAY = timezone.localdate()


def ago(days):
    return TODAY - datetime.timedelta(days=days)


def row(**overrides):
    """One valid row, keyed by column header. Pass column keys (see importer.COLUMNS): buyer=..., reply_date=..."""
    data = {"factory": "FA", "issue_date": ago(30), "buyer": "Example Buyer Ltd", "complaint_type": "Packaging"}
    data.update(overrides)
    return {importer.BY_KEY[key].header: value for key, value in data.items()}


def workbook(rows, headers=None, sheet="Complaints"):
    headers = headers or HEADERS
    book = Workbook()
    sheet_ = book.active
    sheet_.title = sheet
    sheet_.append(headers)
    for data in rows:
        sheet_.append([data.get(header) for header in headers])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def upload(client, content, name="past.xlsx"):
    return client.post(reverse("import_home"), {"file": SimpleUploadedFile(name, content)})


def batch_of(response):
    assert response.status_code == 302, response.content.decode()[:500]
    return ImportBatch.objects.get(pk=int(re.search(r"/import/(\d+)/", response["Location"]).group(1)))


def preview(client, batch):
    return client.get(reverse("import_preview", args=[batch.pk])).content.decode()


def confirm(client, batch):
    return client.post(reverse("import_preview", args=[batch.pk]), {"confirm": "1"}, follow=True)


def import_rows(as_user, rows, **kwargs):
    client = as_user("mr")
    batch = batch_of(upload(client, workbook(rows, **kwargs)))
    confirm(client, batch)
    return fresh_batch(batch)


def fresh_batch(batch):
    return ImportBatch.objects.get(pk=batch.pk)


def problems(client, content):
    """Upload a file that must not be importable, and return the text of the preview page."""
    batch = batch_of(upload(client, content))
    html = preview(client, batch)
    assert "Nothing can be imported until every row is correct" in html or "alert error" in html
    return html


# --- The template ----------------------------------------------------------------------------------------------


def test_the_template_has_the_columns_the_instructions_and_the_lists(as_user):
    response = as_user("mr").get(reverse("import_template"))
    assert response["Content-Type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert response["Content-Disposition"] == 'attachment; filename="complaint-import-template.xlsx"'
    book = load_workbook(io.BytesIO(response.content))
    assert book.sheetnames == ["Instructions", "Complaints", "Lists"]
    sheet = book["Complaints"]
    assert [cell.value for cell in sheet[1]] == HEADERS and sheet.max_row >= 2
    assert sheet.freeze_panes == "B2" and sheet.data_validations.dataValidation  # drop-downs
    lists = {cell.value: [c.value for c in book["Lists"][cell.column_letter][1:] if c.value] for cell in book["Lists"][1]}
    assert lists["Factory"] == ["FA", "FB", "FC", "FD", "FE"] and "Packaging" in lists["Type"]
    assert "Packaging - Leaking" in lists["Sub-type"] and lists["Packing"] == ["Bulk", "Pack"]
    guide = [[c.value for c in r] for r in book["Instructions"].iter_rows()]
    assert any(r[0] == "Complaint type" and r[1] == "Yes" for r in guide)
    assert any("Only the dark columns" in str(r[0]) for r in guide)


def test_the_required_columns_are_marked_in_the_template(as_user):
    book = load_workbook(io.BytesIO(as_user("mr").get(reverse("import_template")).content))
    dark = {cell.value for cell in book["Complaints"][1] if cell.fill.start_color.rgb.endswith("7A4A10")}
    assert dark == {"Factory", "Issue date", "Complaint from", "Complaint type"}


def test_a_blank_template_says_it_has_no_complaints(as_user):
    client = as_user("mr")
    blank = client.get(reverse("import_template")).content
    html = preview(client, batch_of(upload(client, blank)))
    assert "The sheet has no complaints" in html


def test_the_template_follows_the_lists_in_settings(as_user):
    from complaints.models import ComplaintType

    ComplaintType.objects.create(name="Regulatory", sort_order=9)
    book = load_workbook(io.BytesIO(as_user("mr").get(reverse("import_template")).content))
    assert "Regulatory" in [c.value for c in book["Lists"]["E"]]


def test_only_the_coordinator_can_import(as_user):
    for username in ("management", "fa.pic", "fa.hod", "sales"):
        client = as_user(username)
        for name in ("import_home", "import_template"):
            assert client.get(reverse(name)).status_code == 302, (username, name)
        assert client.post(reverse("import_home"), {"file": SimpleUploadedFile("x.xlsx", b"x")}).status_code == 302
    assert not ImportBatch.objects.exists()
    assert "Import</a>" not in as_user("fa.pic").get(reverse("home")).content.decode()
    assert re.search(r'href="/import/" class="active"', as_user("mr").get(reverse("import_home")).content.decode())


# --- Importing a good file ----------------------------------------------------------------------------------------


def test_checking_a_file_saves_nothing(as_user):
    client = as_user("mr")
    batch = batch_of(upload(client, workbook([row(), row(buyer="Second Buyer")])))
    html = preview(client, batch)
    assert batch.status == ImportBatch.Status.PREVIEW and not Complaint.objects.exists()
    assert "What would be imported" in html and "Import 2 complaints" in html
    assert re.search(r"<b>2</b><span>Rows with a complaint", html) and re.search(r"<b>0</b><span>Rows with errors", html)


def test_importing_creates_the_complaints_with_references_in_date_order(as_user):
    # The rows are in the wrong order on purpose.
    batch = import_rows(as_user, [row(issue_date=ago(10)), row(issue_date=ago(40)), row(issue_date=ago(25), factory="FB")])
    assert batch.status == ImportBatch.Status.IMPORTED and batch.imported_count == 3 and batch.rows_total == 3
    by_date = list(Complaint.objects.filter(factory__code="FA").order_by("created_at"))
    year_old, year_new = by_date[0].created_at.year, by_date[1].created_at.year
    assert by_date[0].reference == f"FA/{year_old}/0001"
    assert by_date[1].reference == (f"FA/{year_new}/0002" if year_new == year_old else f"FA/{year_new}/0001")
    assert Complaint.objects.get(factory__code="FB").reference.startswith("FB/") and Complaint.objects.get(factory__code="FB").reference.endswith("/0001")


def test_the_running_numbers_carry_on_after_an_import(as_user):
    import_rows(as_user, [row(issue_date=ago(20)), row(issue_date=ago(10))])
    lodge(as_user("sales"), submit="1")
    new = Complaint.objects.latest("id")
    assert not new.is_imported and new.reference.endswith(f"/{3:04d}") and new.reference.startswith(f"FA/{TODAY.year}/")


def test_each_complaint_gets_the_stage_its_dates_imply(as_user):
    import_rows(
        as_user,
        [
            row(buyer="a submitted one"),
            row(buyer="an investigated one", correction="Replaced it", issue_date=ago(60)),
            row(buyer="a replied one", reply_date=ago(20), hod_decision="Valid", issue_date=ago(50)),
            row(buyer="a closed one", closed_date=ago(5), reply_date=ago(20), hod_decision="Invalid", issue_date=ago(70)),
        ],
    )
    stages = {c.buyer: c.status for c in Complaint.objects.all()}
    assert stages == {
        "a submitted one": "submitted", "an investigated one": "investigation",
        "a replied one": "response_sent", "a closed one": "closed",
    }  # fmt: skip


def test_the_related_records_are_made_from_the_columns(as_user):
    import_rows(
        as_user,
        [
            row(
                legacy_reference="OLD-17", lodger="A. Tan", issue_date=ago(60),
                product_name="Refined oil", delivery_date=ago(70), contract_number=5521, affected_quantity="12.5", quantity_unit="MT",
                transport_type="local delivery - bulk", vehicle_number="ABC 1234", sample_sent="yes", contact_email="b@example.com",
                lab_date=ago(55), ims_date=ago(58), ims_remarks="Please check",
                root_cause="Weld", correction="Replaced", corrective_action="Audit", reply_date=ago(40),
                hod_decision="Valid", first_verified=ago(30), second_verified=ago(10), closed_date=ago(9),
                remarks="Effective",
            )
        ],
    )  # fmt: skip
    c = Complaint.objects.get()
    assert (c.is_imported, c.legacy_reference, c.imported_lodger_name) == (True, "OLD-17", "A. Tan")
    assert c.contract_number == "5521" and str(c.affected_quantity) == "12.500" and c.quantity_unit == "mt"
    assert c.transport_type == "local_bulk" and c.sample_sent is True and c.contact_email == "b@example.com"
    assert c.created_at == timezone.make_aware(datetime.datetime.combine(ago(60), datetime.time(9, 0)))
    assert c.submitted_at == c.created_at and c.lodged_by.username == "mr"
    assert c.coordinator_remarks == "Please check" and c.received_by.username == "mr" and c.acknowledged_at.date() == ago(58)
    assert (c.investigation.root_cause, c.investigation.correction, c.investigation.corrective_action) == ("Weld", "Replaced", "Audit")
    assert c.lab.acknowledged_on == ago(55)
    assert c.reply.sent_at.date() == ago(40) and "before the platform was used" in c.reply.body
    assert c.verification.first_verified_date == ago(30) and c.verification.second_verified_date == ago(10)
    assert c.verification.closed_at.date() == ago(9) and c.verification.closing_comment == "Effective"
    assert c.status == "closed" and c.hod_decision == "valid"
    event = c.events.get()
    assert event.action == ComplaintEvent.Action.IMPORTED and "row 2" in event.comment and "Closed" in event.comment


def test_the_old_number_of_two_factories_may_be_the_same(as_user):
    batch = import_rows(as_user, [row(legacy_reference="7"), row(legacy_reference="7", factory="FB")])
    assert batch.imported_count == 2


def test_dates_can_be_typed_in_the_usual_ways(as_user):
    day = ago(40)
    import_rows(
        as_user,
        [
            row(issue_date=day.strftime("%d/%m/%Y"), buyer="a"),
            row(issue_date=day.strftime("%Y-%m-%d"), buyer="b"),
            row(issue_date=datetime.datetime.combine(day, datetime.time(14, 30)), buyer="c"),
            row(issue_date=day.strftime("%d %b %Y"), buyer="d"),
        ],
    )
    issued = {c.buyer: timezone.localtime(c.created_at) for c in Complaint.objects.all()}
    assert {v.date() for v in issued.values()} == {day}
    assert issued["c"].hour == 14 and issued["a"].hour == 9


def test_columns_may_be_in_any_order_with_loose_headers_and_extra_columns(as_user):
    headers = ["complaint type", "COMPLAINT FROM *", "Issue Date", "factory", "Customer complaint no.", "My own notes"]
    data = [{"complaint type": "Packaging", "COMPLAINT FROM *": "Loose Buyer", "Issue Date": ago(9), "factory": "Factory B",
             "Customer complaint no.": "N-9", "My own notes": "ignored"}]  # fmt: skip
    client = as_user("mr")
    batch = batch_of(upload(client, workbook(data, headers=headers)))
    html = preview(client, batch)
    assert "These columns are not in the template and are ignored: My own notes" in html
    confirm(client, batch)
    c = Complaint.objects.get()
    assert (c.buyer, c.factory.code, c.legacy_reference) == ("Loose Buyer", "FB", "N-9")


def test_blank_rows_are_skipped_and_the_first_sheet_is_used(as_user):
    content = workbook([row(), {}, row(buyer="Another"), {}], sheet="Old log")
    batch = import_rows_content(as_user, content)
    assert batch.imported_count == 2


def import_rows_content(as_user, content):
    client = as_user("mr")
    batch = batch_of(upload(client, content))
    confirm(client, batch)
    return fresh_batch(batch)


def test_no_emails_are_sent_and_nothing_is_logged_as_sent(as_user):
    mail.outbox.clear()
    import_rows(as_user, [row(), row(buyer="B", closed_date=ago(5), hod_decision="Valid")])
    assert mail.outbox == [] and not NotificationLog.objects.exists()


def test_an_open_imported_complaint_starts_its_reminder_clock_at_the_import(as_user, user_of):
    import_rows(as_user, [row(issue_date=ago(120), correction="Done")])
    c = Complaint.objects.get()
    assert c.status == "investigation" and (timezone.now() - c.status_changed_at) < datetime.timedelta(minutes=5)
    assert not c.is_overdue and c.days_waiting == 0
    assert reminders.send_due_reminders(timezone.now()) == []
    (item,) = reminders.send_due_reminders(timezone.now() + datetime.timedelta(days=3))
    assert [u.username for u in item["recipients"]] == ["mr"] and "Nobody is set up" in item["note"]  # no PIC yet


def test_a_closed_imported_complaint_keeps_its_closing_date_as_the_stage_date(as_user):
    import_rows(as_user, [row(closed_date=ago(5), hod_decision="Valid", issue_date=ago(50))])
    c = Complaint.objects.get()
    assert timezone.localtime(c.status_changed_at).date() == ago(5) and not c.is_overdue


def test_an_open_imported_complaint_can_be_taken_on_by_the_normal_workflow(as_user, user_of):
    import_rows(as_user, [row(buyer="Carry on Ltd", correction="Started")])
    c = Complaint.objects.get()
    from accounts.models import FactoryPIC as PIC

    workflow.assign(c, user_of("mr"), list(PIC.objects.filter(factory=c.factory, user__username="fa.pic")), "Please continue")
    c = Complaint.objects.get()
    assert c.status == "investigation" and c.assignments.count() == 1
    workflow.save_investigation(c, user_of("fa.pic"), "Cause", "Fix", "Prevent", "internal")
    workflow.submit_to_hod(Complaint.objects.get(), user_of("fa.pic"))
    assert Complaint.objects.get().status == "hod_review"


def test_a_deleted_row_is_imported_as_deleted_with_its_reason(as_user):
    import_rows(as_user, [row(deleted="Yes", deleted_reason="Entered twice")])
    c = Complaint.objects.get()
    assert c.is_deleted and c.deleted_reason == "Entered twice" and c.deleted_by.username == "mr"


def test_imported_complaints_appear_in_the_dashboard_the_log_and_the_report(as_user, user_of):
    import_rows(
        as_user,
        [
            row(lodger="A. Tan", legacy_reference="OLD-1", closed_date=ago(5),
                hod_decision="Valid", issue_date=ago(50), correction="Replaced", remarks="Effective", reply_date=ago(20)),  # fmt: skip
            row(buyer="Open one", issue_date=ago(20)),
        ],
    )
    counts = reporting.summary(reporting.scoped(user_of("mr")))
    assert counts["total"] == 2 and counts["valid_closed"] == 1 and counts["under_investigation"] == 1
    client = as_user("mr")
    log = client.get(reverse("complaint_log")).content.decode()
    body = log.split("<tbody>")[1].split("</tbody>")[0]
    assert "A. Tan" in body and "Not recorded" in body and "Sample Coordinator" not in body  # never the importer
    sheet = load_workbook(io.BytesIO(client.get(reverse("log_export")).content))["Complaint log"]
    columns = {cell.value: cell.column for cell in sheet[1]}
    assert sheet.cell(row=2, column=columns["Complaint received by"]).value == "A. Tan"
    c = Complaint.objects.get(legacy_reference="OLD-1")
    page = client.get(reverse("complaint_detail", args=[c.pk])).content.decode()
    assert "Imported from the previous log" in page and "OLD-1" in page and "A. Tan" in page
    assert "Management Representative" not in page.split("Lodged by")[1][:200]  # not the importer's department
    head = page.split('<ol class="stepper"')[0]
    assert "Raised by A. Tan" in head and "Sample Coordinator" not in head.split("Raised by")[1][:60]
    closing = page.split("Effectiveness and closing")[1].split("Notifications")[0]
    assert "by Sample Coordinator" not in closing
    assert "Sample Coordinator" not in page.split("Reply sent to the customer")[1].split("Effectiveness")[0]
    from pypdf import PdfReader

    pdf = client.get(reverse("complaint_pdf", args=[c.pk])).content
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
    assert "Imported from the previous log" in text and "OLD-1" in text


# --- A file with problems -----------------------------------------------------------------------------------------------------


def test_every_kind_of_mistake_is_reported_with_its_row_and_column(as_user):
    bad = [
        row(factory="ZZ"),                                                       # row 2
        row(complaint_type="Nonsense"),                                          # row 3
        row(complaint_sub_type="Logistic - Shortage"),                                     # row 4: not a Packaging sub-type
        row(issue_date="not a date"),                                            # row 5
        row(issue_date=TODAY + datetime.timedelta(days=3)),                      # row 6
        row(hod_decision="Maybe"),                                           # row 7
        row(closed_date=ago(3)),                                            # row 8: closed, no decision
        row(reply_date=ago(3)),                                # row 9: replied, no decision
        row(deleted="Yes"),                                                      # row 10: no reason
        row(contact_email="not-an-email"),                                               # row 11
        row(affected_quantity="lots"),                                           # row 12
        row(quantity_unit="barrels"),                                                     # row 13
        row(packing_type="Loose"),                                               # row 14
        row(transport_type="Teleport"),                                          # row 15
        row(buyer=None),                                                # row 16
        row(buyer="x" * 201),                                           # row 17
        row(sample_sent="perhaps"),                                              # row 18
        row(complaint_method="Carrier pigeon"),                                  # row 19
        row(closed_date="soon", hod_decision="Valid"),                  # row 20
    ]  # fmt: skip
    client = as_user("mr")
    html = problems(client, workbook(bad))
    expected = {
        2: ("Factory", "is not a factory"), 3: ("Complaint type", "is not a complaint type"),
        4: ("Sub-type", "is not a sub-type of Packaging"), 5: ("Issue date", "is not a date"),
        6: ("Issue date", "in the future"), 7: ("Valid or invalid", "is not Valid or Invalid"),
        8: ("Valid or invalid", "Say whether"), 9: ("Valid or invalid", "Say whether"),
        10: ("Reason delete", "reason for deleting"), 11: ("Email", "is not an email address"),
        12: ("Affected quantity", "is not a number"), 13: ("Unit", "is not Metric Ton or Kg"),
        14: ("Packing type", "is not Bulk or Pack"), 15: ("Transport type", "four transport types"),
        16: ("Complaint from", "Give the buyer"), 17: ("Complaint from", "Too long"),
        18: ("Sample sent to factory", "is not Yes or No"), 19: ("Complaint method", "is not a complaint method"),
        20: ("Date case closed", "is not a date"),
    }  # fmt: skip
    found = re.findall(r'<tr><td class="num">(\d+)</td><td>([^<]+)</td><td>([^<]+)</td></tr>', html)
    for number, (column, text) in expected.items():
        assert any(int(n) == number and col == column and text in msg.replace("&#x27;", "'") for n, col, msg in found), (number, column, text)
    assert not Complaint.objects.exists()


def test_a_file_with_errors_cannot_be_imported_even_by_posting(as_user):
    client = as_user("mr")
    batch = batch_of(upload(client, workbook([row(), row(factory="ZZ")])))
    response = confirm(client, batch)
    assert "The file still has errors" in response.content.decode()
    assert not Complaint.objects.exists() and fresh_batch(batch).status == ImportBatch.Status.PREVIEW
    assert 'name="confirm"' not in preview(client, batch)


def test_two_rows_with_the_same_old_number_for_one_factory_are_refused(as_user):
    html = problems(as_user("mr"), workbook([row(legacy_reference="12"), row(legacy_reference="12")]))
    assert "appears twice for Factory A in this file" in html


def test_a_file_cannot_be_imported_twice(as_user):
    import_rows(as_user, [row(legacy_reference="OLD-5")])
    html = problems(as_user("mr"), workbook([row(legacy_reference="old-5")]))
    assert "was already imported for Factory A" in html
    assert Complaint.objects.count() == 1


def test_pressing_import_twice_imports_once(as_user):
    client = as_user("mr")
    batch = batch_of(upload(client, workbook([row()])))
    confirm(client, batch)
    again = confirm(client, batch)
    assert Complaint.objects.count() == 1
    assert "Imported 1 complaint" in again.content.decode() or fresh_batch(batch).imported_count == 1


def test_the_template_headers_are_needed(as_user):
    html = problems(as_user("mr"), workbook([{"A": 1}], headers=["A", "Factory", "Issue date"]))
    assert "Missing: Complaint from, Complaint type" in html


def test_a_file_that_is_not_a_workbook_is_refused(as_user):
    client = as_user("mr")
    assert "could not be read as an Excel" in problems(client, b"this is not a workbook")
    csv = client.post(reverse("import_home"), {"file": SimpleUploadedFile("past.csv", b"a,b")})
    assert csv.status_code == 200 and "ending in .xlsx" in csv.content.decode()


def test_a_file_that_is_too_big_or_has_too_many_rows_is_refused(as_user, monkeypatch):
    client = as_user("mr")
    monkeypatch.setattr(importer, "MAX_ROWS", 2)
    assert "at most 2 at a time" in problems(client, workbook([row(), row(), row()]))
    monkeypatch.setattr(importer, "MAX_FILE_MB", 0)
    assert "larger than 0 MB" in client.post(reverse("import_home"), {"file": SimpleUploadedFile("big.xlsx", b"x")}).content.decode()


def test_warnings_are_shown_but_do_not_stop_the_import(as_user):
    client = as_user("mr")
    rows = [
        row(issue_date=ago(400), buyer="old"),
        row(delivery_date=ago(1), issue_date=ago(10), buyer="late"),
        row(hod_decision="Invalid", buyer="no reason"),
        row(lab_date=ago(5), sample_sent="No", buyer="sample"),
    ]
    batch = batch_of(upload(client, workbook(rows)))
    html = preview(client, batch)
    for text in ("more than 6 months ago", "delivery date is after the issue date", "invalid but no reason", "lab date but Sample sent is No"):
        assert text in html, text
    assert re.search(r"<b>4</b><span>Warnings", html) and 'name="confirm"' in html
    confirm(client, batch)
    assert Complaint.objects.count() == 4


def test_nothing_is_saved_if_the_import_fails_half_way(as_user, monkeypatch):
    client = as_user("mr")
    batch = batch_of(upload(client, workbook([row(issue_date=ago(20)), row(issue_date=ago(10)), row(issue_date=ago(5))])))
    real, calls = importer._create, []

    def failing(*args, **kwargs):
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError("the database went away")
        return real(*args, **kwargs)

    monkeypatch.setattr(importer, "_create", failing)
    with pytest.raises(RuntimeError):
        confirm(client, batch)
    assert not Complaint.objects.exists() and fresh_batch(batch).status == ImportBatch.Status.PREVIEW
    # The reference numbers are not used up either.
    monkeypatch.setattr(importer, "_create", real)
    confirm(client, batch)
    assert sorted(Complaint.objects.values_list("reference", flat=True))[0].endswith("0001")


def test_a_file_can_be_discarded(as_user):
    client = as_user("mr")
    batch = batch_of(upload(client, workbook([row()])))
    path = batch.file.name
    client.post(reverse("import_preview", args=[batch.pk]), {"discard": "1"})
    batch = fresh_batch(batch)
    assert batch.status == ImportBatch.Status.DISCARDED and not batch.file.storage.exists(path)
    assert not Complaint.objects.exists()
    assert "was discarded" in client.get(reverse("import_preview", args=[batch.pk])).content.decode()
    assert "Nothing from it was imported" in client.get(reverse("import_preview", args=[batch.pk])).content.decode()


def test_the_page_lists_earlier_uploads_and_the_result(as_user):
    client = as_user("mr")
    batch = import_rows(as_user, [row(), row(buyer="Two")])
    html = client.get(reverse("import_home")).content.decode()
    assert "past.xlsx" in html and "Imported" in html
    done = client.get(reverse("import_preview", args=[batch.pk])).content.decode()
    assert re.search(r"<b>2</b><span>Complaints imported", done) and "FA/" in done


def test_the_import_is_in_the_audit_log(as_user):
    from audit.models import AuditLog

    import_rows(as_user, [row()])
    models = set(AuditLog.objects.values_list("content_type__model", flat=True))
    assert {"importbatch", "complaint", "investigation"} <= models or {"importbatch", "complaint"} <= models
