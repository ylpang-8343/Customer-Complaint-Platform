"""Step 5: the dashboard, the complaint log and the Excel export."""

import datetime
import io
import re

import pytest
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from accounts.models import Factory
from complaints import reporting, workflow
from complaints.models import Complaint, ComplaintSubType, ComplaintType, LabTest
from tests.test_complaints import as_user, complete_data, lodge  # noqa: F401  (fixtures and helpers)
from tests.test_workflow import (  # noqa: F401
    assigned,
    at_fm,
    at_hod,
    fresh,
    replied,
    submitted,
)

DAY = datetime.timedelta(days=1)
EXPECTED_HEADERS = [
    "No.", "Customer complaint no.", "Complaint from", "Complaint received by", "Issue date", "Product",
    "Delivery date", "Lab acknowledgement date", "Date reply to buyer / trader", "IMS acknowledgement date",
    "IMS remarks", "Type of problem", "Types of valid complaint", "Correction", "Corrective action",
    "First verified date", "Second verified date", "Date case closed", "Remarks", "Deleted", "Reason delete",
]  # fmt: skip


@pytest.fixture
def portfolio(as_user, user_of):
    """Five submitted complaints in every state, and a draft that must never be counted."""
    mr, fa_pic = user_of("mr"), user_of("fa.pic")
    logistic = ComplaintType.objects.get(name="Logistic")
    shortage = ComplaintSubType.objects.get(name="Logistic - Shortage")

    closed = replied(as_user, user_of)  # Packaging, valid, closed
    workflow.save_verification(closed, mr, datetime.date(2026, 12, 1), "No repeat", datetime.date(2027, 3, 1), "Still fine")
    workflow.close_case(fresh(closed), mr, "Effective")

    other_factory = submitted(  # waiting for the Coordinator, Factory B, Logistic
        as_user, factory=Factory.objects.get(code="FB").pk, complaint_type=logistic.pk,
        complaint_sub_type=shortage.pk, buyer="Beta Trading",
    )  # fmt: skip
    under_investigation = at_hod(as_user, user_of)  # HOD review, no decision yet
    invalid = at_fm(as_user, user_of, decision="invalid")  # FM approval, invalid
    deleted = assigned(as_user, user_of)
    workflow.delete(deleted, mr, "Duplicate of an earlier complaint")
    lodge(as_user("sales"), save="1")  # a draft
    return {
        "closed": fresh(closed), "other": other_factory, "investigating": fresh(under_investigation),
        "invalid": fresh(invalid), "deleted": fresh(deleted), "pic": fa_pic,
    }  # fmt: skip


def figures(user, **scope):
    return reporting.summary(reporting.scoped(user, **scope))


# --- The figures ----------------------------------------------------------------------------------


def test_the_summary_counts_every_submitted_complaint_once(portfolio, user_of):
    counts = figures(user_of("mr"))
    assert counts["total"] == 5  # the draft is not counted
    assert (counts["valid"], counts["invalid"], counts["under_investigation"], counts["deleted"]) == (1, 1, 2, 1)
    assert counts["valid"] + counts["invalid"] + counts["under_investigation"] + counts["deleted"] == counts["total"]
    assert (counts["valid_in_progress"], counts["valid_closed"]) == (0, 1)


def test_a_valid_complaint_is_in_progress_until_it_is_closed(portfolio, user_of):
    complaint = portfolio["closed"]
    Complaint.objects.filter(pk=complaint.pk).update(status=Complaint.Status.RESPONSE_SENT)
    counts = figures(user_of("mr"))
    assert (counts["valid_in_progress"], counts["valid_closed"]) == (1, 0)


def test_complaints_by_type_and_valid_complaints_by_type(portfolio, user_of):
    queryset = reporting.scoped(user_of("mr"))
    by_type = dict(reporting.by_type(queryset))
    assert by_type["Packaging"] == 4 and by_type["Logistic"] == 1 and by_type["Product Quality"] == 0
    assert dict(reporting.valid_by_type(queryset)) == {
        "Product Quality": 0, "Packaging": 1, "Logistic": 0, "Documentation": 0, "Others": 0,
    }  # fmt: skip


def test_the_stage_counts_follow_the_process_order(portfolio, user_of):
    stages = reporting.stage_counts(reporting.scoped(user_of("mr")))
    assert [label for label, _ in stages] == [
        "Submitted", "PIC investigation", "Pending SCAR from Supplier", "HOD review", "FM approval",
        "Approved, reply due", "Customer Response Sent", "Closed",
    ]  # fmt: skip
    assert dict(stages) == {
        "Submitted": 1, "PIC investigation": 0, "Pending SCAR from Supplier": 0, "HOD review": 1, "FM approval": 1,
        "Approved, reply due": 0, "Customer Response Sent": 0, "Closed": 1,
    }  # fmt: skip  (the deleted complaint is not in any stage)


def test_open_complaint_aging(portfolio, user_of):
    now = timezone.now()
    Complaint.objects.filter(pk=portfolio["other"].pk).update(submitted_at=now - 20 * DAY)
    Complaint.objects.filter(pk=portfolio["investigating"].pk).update(submitted_at=now - 2 * DAY)
    rows, bands = reporting.aging(reporting.scoped(user_of("mr")), now)
    # open = not closed, not deleted
    assert [r["complaint"].pk for r in rows][0] == portfolio["other"].pk  # oldest first
    assert {r["complaint"].pk for r in rows} == {portfolio["other"].pk, portfolio["investigating"].pk, portfolio["invalid"].pk}
    assert dict(bands) == {"0-3 days": 2, "4-7 days": 0, "8-14 days": 0, "15-30 days": 1, "Over 30 days": 0}
    oldest = rows[0]
    assert oldest["days_open"] == 20 and oldest["role"] == "Coordinator"
    assert "Sample Coordinator" in oldest["people"]
    waiting = {r["complaint"].pk: r["role"] for r in rows}
    assert waiting[portfolio["investigating"].pk] == "HOD" and waiting[portfolio["invalid"].pk] == "FM"


def test_reasons_for_invalid_and_deleted_complaints(portfolio, user_of):
    rows = reporting.reasons(reporting.scoped(user_of("mr")))
    assert {(kind, c.pk, reason) for kind, c, reason in rows} == {
        ("Invalid", portfolio["invalid"].pk, "Not our fault"),
        ("Deleted", portfolio["deleted"].pk, "Duplicate of an earlier complaint"),
    }


def test_the_filters_narrow_the_figures(portfolio, user_of):
    fb = Factory.objects.get(code="FB")
    assert figures(user_of("mr"), factories=[fb])["total"] == 1
    assert figures(user_of("mr"), date_from=datetime.date(2999, 1, 1))["total"] == 0
    assert figures(user_of("mr"), date_to=datetime.date(2000, 1, 1))["total"] == 0
    today = timezone.localdate()
    assert figures(user_of("mr"), date_from=today, date_to=today)["total"] == 5


def test_each_role_sees_only_its_own_scope(portfolio, user_of):
    assert figures(user_of("mr"))["total"] == figures(user_of("management"))["total"] == 5
    assert figures(user_of("fa.pic"))["total"] == 4  # Factory A only
    assert figures(user_of("fb.hod"))["total"] == 1  # Factory B only
    assert figures(user_of("fc.pic"))["total"] == 0
    assert figures(user_of("sales"))["total"] == 5  # a lodger sees what they lodged


# --- The dashboard page ---------------------------------------------------------------------------------


def test_the_dashboard_shows_the_figures_for_everyone(portfolio, as_user):
    for username in ("mr", "management", "fa.pic", "fa.hod", "fa.fm", "fb.hod", "fc.pic", "sales"):
        response = as_user(username).get(reverse("dashboard"))
        assert response.status_code == 200, username
    html = as_user("mr").get(reverse("dashboard")).content.decode()
    tiles = dict(re.findall(r'<b>(\d+)</b><span>([^<]+)</span>', html)[i][::-1] for i in range(7))
    assert tiles["Total number of complaints"] == "5" and tiles["Deleted"] == "1" and tiles["Under investigation"] == "2"
    assert "Complaints by type" in html and "Open complaint aging" in html and "Valid complaints by type" in html
    assert "Duplicate of an earlier complaint" in html and "Not our fault" in html  # the reasons


def test_the_dashboard_filters_by_factory_and_period(portfolio, as_user):
    client = as_user("mr")
    fb = Factory.objects.get(code="FB").pk
    html = client.get(reverse("dashboard"), {"factories": [fb]}).content.decode()
    assert re.search(r"<b>1</b><span>Total number of complaints", html)
    assert "Factory B" in html.split("Showing:")[1].split("</div>")[0]
    html = client.get(reverse("dashboard"), {"date_from": "2999-01-01"}).content.decode()
    assert re.search(r"<b>0</b><span>Total number of complaints", html) and "No open complaints." in html
    bad = client.get(reverse("dashboard"), {"date_from": "2026-12-01", "date_to": "2026-01-01"}).content.decode()
    assert "The end date is before the start date." in bad


def test_a_user_only_gets_factory_choices_they_may_use(portfolio, as_user):
    pic = as_user("fa.pic").get(reverse("dashboard")).content.decode()
    assert "Factory A" in pic and "Factory B" not in pic.split('class="chips"')[1].split("</form>")[0]
    lodger = as_user("sales").get(reverse("dashboard")).content.decode()
    assert 'class="chips"' not in lodger  # a lodger has no factory to choose
    # A factory the user may not see cannot be forced through the address.
    fb = Factory.objects.get(code="FB").pk
    html = as_user("fa.pic").get(reverse("dashboard"), {"factories": [fb]}).content.decode()
    assert re.search(r"<b>0</b><span>Total number of complaints", html) or "Select a valid choice" in html


def test_the_bars_are_scaled_to_the_largest(portfolio, as_user):
    html = as_user("mr").get(reverse("dashboard")).content.decode()
    widths = re.findall(r'<li title="Packaging: 4">.*?width: (\d+)%', html, re.S)
    assert widths == ["100"]  # Packaging is the largest type
    assert re.search(r'<li title="Product Quality: 0">', html)  # a zero is written, with no bar


def test_the_menu_links_to_the_dashboard_and_the_log(portfolio, as_user):
    html = as_user("fa.pic").get(reverse("dashboard")).content.decode()
    assert re.search(r'<a href="/dashboard/" class="active" aria-current="page">Dashboard</a>', html)
    html = as_user("fa.pic").get(reverse("complaint_log")).content.decode()
    assert re.search(r'<a href="/log/" class="active" aria-current="page">Complaint log</a>', html)


def test_reports_need_a_sign_in(client, seeded):
    for name in ("dashboard", "complaint_log", "log_export"):
        assert client.get(reverse(name)).status_code == 302


# --- The complaint log ------------------------------------------------------------------------------------------


def test_the_log_has_the_columns_of_the_current_log_without_the_car_number(portfolio, as_user):
    html = as_user("mr").get(reverse("complaint_log")).content.decode()
    headers = re.findall(r"<th[^>]*>([^<]+)</th>", html.split("<thead>")[1].split("</thead>")[0])
    assert headers == EXPECTED_HEADERS
    assert not any("CAR" in header for header in headers)


def test_the_log_lists_every_complaint_oldest_first_and_links_to_each(portfolio, as_user):
    html = as_user("mr").get(reverse("complaint_log")).content.decode()
    refs = re.findall(r'<td><a href="/complaints/\d+/">([A-Z]{2}/\d{4}/\d{4})</a></td>', html)
    assert len(refs) == 5
    numbers = re.findall(r'<td class="num">(\d+)</td>', html.split("<tbody>")[1].split("</tbody>")[0])
    assert numbers[:5] == ["1", "2", "3", "4", "5"]


def test_the_log_takes_each_column_from_the_right_record(portfolio, user_of, as_user):
    closed, deleted = portfolio["closed"], portfolio["deleted"]
    row = dict(zip(EXPECTED_HEADERS[1:], reporting.log_row_text(closed)))
    assert row["Customer complaint no."] == closed.reference
    assert row["Complaint from"] == "Example Buyer Ltd" and row["Complaint received by"] == "Sample Sales"
    assert row["Product"] == "Sample Product" and row["Delivery date"] == "28 Oct 2026"
    assert row["Type of problem"] == "Packaging - Leaking" and row["Types of valid complaint"] == "Packaging"
    assert row["Correction"] == "Replaced the pack" and row["Corrective action"] == "Changed the supplier of seals"
    assert row["First verified date"] == "1 Dec 2026" and row["Second verified date"] == "1 Mar 2027"
    assert row["Date case closed"] and row["Remarks"] == "Effective" and row["Deleted"] == ""
    assert row["IMS remarks"] == "Please investigate" and row["IMS acknowledgement date"]
    assert row["Date reply to buyer / trader"] and row["Lab acknowledgement date"] == ""
    gone = dict(zip(EXPECTED_HEADERS[1:], reporting.log_row_text(deleted)))
    assert gone["Deleted"] == "Yes" and gone["Reason delete"] == "Duplicate of an earlier complaint"
    assert gone["Types of valid complaint"] == ""
    invalid = dict(zip(EXPECTED_HEADERS[1:], reporting.log_row_text(portfolio["invalid"])))
    assert invalid["Types of valid complaint"] == ""  # only complaints the HOD set as valid


def test_the_lab_acknowledgement_date_comes_from_the_laboratory_section(as_user, user_of):
    complaint = assigned(as_user, user_of, "fa.pic", "fa.lab", sample_sent="True")
    ffa = LabTest.objects.get(name="FFA")
    workflow.save_lab(complaint, user_of("fa.lab"), datetime.date(2026, 11, 2), "", {ffa: ("1", "1")})
    row = dict(zip(EXPECTED_HEADERS[1:], reporting.log_row_text(fresh(complaint))))
    assert row["Lab acknowledgement date"] == "2 Nov 2026"


def test_the_log_shows_the_summary_under_the_table(portfolio, as_user):
    html = as_user("mr").get(reverse("complaint_log")).content.decode()
    summary = html.split(">Summary<")[1]
    assert re.search(r"Total number of complaints</td><td class=\"num\"><strong>5</strong>", summary)
    assert re.search(r"Valid complaints closed</td><td class=\"num\"><strong>1</strong>", summary)
    assert "Cases for each type of valid complaint" in html


def test_the_log_is_paged_and_keeps_its_numbers(portfolio, as_user, monkeypatch):
    monkeypatch.setattr("complaints.report_views.LOG_PAGE_SIZE", 2)
    client = as_user("mr")
    page2 = client.get(reverse("complaint_log"), {"page": 2}).content.decode()
    body = page2.split("<tbody>")[1].split("</tbody>")[0]
    assert re.findall(r'<td class="num">(\d+)</td>', body) == ["3", "4"]
    assert "page=2" in client.get(reverse("complaint_log"), {"date_from": "2020-01-01"}).content.decode()


def test_the_log_is_filtered_like_the_dashboard(portfolio, as_user):
    fb = Factory.objects.get(code="FB").pk
    html = as_user("mr").get(reverse("complaint_log"), {"factories": [fb]}).content.decode()
    assert len(re.findall(r'<td><a href="/complaints/\d+/">', html)) == 1
    assert "1 complaint<" in html or "1 complaint " in html


def test_other_factories_do_not_appear_in_a_users_log(portfolio, as_user):
    html = as_user("fb.hod").get(reverse("complaint_log")).content.decode()
    assert len(re.findall(r'<td><a href="/complaints/\d+/">', html)) == 1


# --- The Excel export -----------------------------------------------------------------------------------------------


def workbook_of(response):
    assert response.status_code == 200
    return load_workbook(io.BytesIO(response.content))


def test_the_export_is_an_excel_file_with_the_log_columns(portfolio, as_user):
    response = as_user("mr").get(reverse("log_export"))
    assert response["Content-Type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert response["Content-Disposition"] == f'attachment; filename="complaint-log-{timezone.localdate():%Y-%m-%d}.xlsx"'
    sheet = workbook_of(response)["Complaint log"]
    assert [cell.value for cell in sheet[1]] == EXPECTED_HEADERS
    assert sheet.freeze_panes == "C2" and sheet.auto_filter.ref == "A1:U6"
    assert [sheet.cell(row=r, column=1).value for r in range(2, 7)] == [1, 2, 3, 4, 5]


def test_the_export_keeps_real_dates_and_the_right_values(portfolio, as_user):
    sheet = workbook_of(as_user("mr").get(reverse("log_export")))["Complaint log"]
    columns = {cell.value: cell.column for cell in sheet[1]}
    first = 2  # the oldest complaint is the closed one
    assert sheet.cell(row=first, column=columns["Customer complaint no."]).value == portfolio["closed"].reference
    issue = sheet.cell(row=first, column=columns["Issue date"])
    assert isinstance(issue.value, datetime.datetime) and issue.number_format == "dd mmm yyyy hh:mm"
    delivery = sheet.cell(row=first, column=columns["Delivery date"])
    assert delivery.value.date() == datetime.date(2026, 10, 28) and delivery.number_format == "dd mmm yyyy"
    assert sheet.cell(row=first, column=columns["First verified date"]).value.date() == datetime.date(2026, 12, 1)
    assert sheet.cell(row=first, column=columns["Remarks"]).value == "Effective"
    deleted_row = next(r for r in range(2, 7) if sheet.cell(row=r, column=columns["Deleted"]).value == "Yes")
    assert sheet.cell(row=deleted_row, column=columns["Reason delete"]).value == "Duplicate of an earlier complaint"


def test_the_export_has_the_summary_under_the_table(portfolio, as_user):
    sheet = workbook_of(as_user("mr").get(reverse("log_export")))["Complaint log"]
    cells = {sheet.cell(row=r, column=2).value: sheet.cell(row=r, column=3).value for r in range(7, sheet.max_row + 1)}
    assert cells["Total number of complaints"] == 5 and cells["Valid"] == 1 and cells["Invalid"] == 1
    assert cells["Under investigation"] == 2 and cells["Deleted"] == 1
    assert cells["Valid complaints in progress"] == 0 and cells["Valid complaints closed"] == 1
    assert cells["Packaging"] == 1 and cells["Logistic"] == 0
    note = [v for v in cells if isinstance(v, str) and v.startswith("Exported by")][0]
    assert "Sample Coordinator" in note and "all factories you can see" in note


def test_the_export_follows_the_filters_and_the_users_scope(portfolio, as_user):
    fb = Factory.objects.get(code="FB").pk
    sheet = workbook_of(as_user("mr").get(reverse("log_export"), {"factories": [fb]}))["Complaint log"]
    assert sheet.cell(row=2, column=2).value.startswith("FB/") and sheet.cell(row=3, column=1).value is None
    assert "Factory B" in [v for v in (sheet.cell(row=r, column=2).value for r in range(1, sheet.max_row + 1)) if v and str(v).startswith("Exported")][0]
    own = workbook_of(as_user("fb.hod").get(reverse("log_export")))["Complaint log"]
    assert own.cell(row=2, column=2).value.startswith("FB/") and own.cell(row=3, column=1).value is None
    empty = workbook_of(as_user("fc.pic").get(reverse("log_export")))["Complaint log"]
    assert empty.cell(row=2, column=1).value is None


def test_text_that_looks_like_a_formula_stays_text(as_user, user_of):
    lodge(as_user("sales"), submit="1", buyer='=HYPERLINK("http://example.invalid","click")', description="@SUM(1+1)")
    sheet = workbook_of(as_user("mr").get(reverse("log_export")))["Complaint log"]
    columns = {cell.value: cell.column for cell in sheet[1]}
    cell = sheet.cell(row=2, column=columns["Complaint from"])
    assert cell.data_type == "s" and cell.value.startswith("=HYPERLINK")


def test_an_export_of_nothing_still_has_its_headers(seeded, as_user):
    sheet = workbook_of(as_user("mr").get(reverse("log_export")))["Complaint log"]
    assert [cell.value for cell in sheet[1]] == EXPECTED_HEADERS and sheet.cell(row=2, column=1).value is None
