import datetime
from decimal import Decimal

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from accounts.models import Factory
from complaints.models import (
    Attachment,
    Complaint,
    ComplaintMethod,
    ComplaintSubType,
    ComplaintType,
    ReferenceCounter,
)

PASSWORD = "demo-pass-123"


def complete_data(**overrides):
    """POST data for a complete complaint about a leaking flexibag shipped in a container."""
    data = {
        "factory": Factory.objects.get(code="FA").pk,
        "buyer": "Example Buyer Ltd",
        "contract_number": "SC-000123",
        "product_name": "Sample Product",
        "delivery_date": "2026-10-28",
        "packing_type": "pack",
        "pack_size": "Flexibag",
        "affected_quantity": "20",
        "quantity_unit": "mt",
        "transport_type": "shipment_container",
        "vessel_name": "MV Example",
        "container_number": "ABCD1234567",
        "loading_port": "Example Loading Port",
        "discharge_port": "Example Discharge Port",
        "complaint_type": ComplaintType.objects.get(name="Packaging").pk,
        "complaint_sub_type": ComplaintSubType.objects.get(name="Packaging - Leaking").pk,
        "sample_sent": "False",
        "description": "Flexibag leaked during discharge.",
        "contact_email": "contact@example.com",
    }
    data.update(overrides)
    return data


@pytest.fixture
def as_user(seeded):
    """Sign in as a demo user in a browser of their own, so cookies never carry over."""

    def sign_in(username):
        client = Client()
        assert client.login(username=username, password=PASSWORD)
        return client

    return sign_in


def lodge(client, **overrides):
    return client.post(reverse("complaint_new"), complete_data(**overrides))


# --- Reference numbers -------------------------------------------------------------


def make_draft(user, factory_code="FA"):
    complaint = Complaint(lodged_by=user, factory=Factory.objects.get(code=factory_code))
    complaint.assign_reference()
    complaint.save()
    return complaint


def test_reference_runs_on_for_each_factory_and_year(user_of):
    sales = user_of("sales")
    assert make_draft(sales, "FA").reference == f"FA/{datetime.date.today().year}/0001"
    assert make_draft(sales, "FB").reference.endswith("/0001")
    assert make_draft(sales, "FA").reference.endswith("/0002")
    assert make_draft(sales, "FA").reference.endswith("/0003")
    assert make_draft(sales, "FB").reference.endswith("/0002")


def test_reference_starts_again_each_year(user_of):
    fa = Factory.objects.get(code="FA")
    last_year = Complaint(lodged_by=user_of("sales"), factory=fa)
    last_year.assign_reference(today=datetime.date(2026, 12, 31))
    this_year = Complaint(lodged_by=user_of("sales"), factory=fa)
    this_year.assign_reference(today=datetime.date(2027, 1, 1))
    assert last_year.reference == "FA/2026/0001"
    assert this_year.reference == "FA/2027/0001"
    assert ReferenceCounter.objects.filter(factory=fa).count() == 2


def test_reference_is_assigned_once_and_needs_a_factory(user_of):
    draft = make_draft(user_of("sales"))
    reference = draft.reference
    draft.assign_reference()
    assert draft.reference == reference
    with pytest.raises(ValidationError):
        Complaint(lodged_by=user_of("sales")).assign_reference()


# --- Mandatory and conditional fields ----------------------------------------------


@pytest.fixture
def complete_complaint(user_of):
    def build(**overrides):
        fields = {
            "lodged_by": user_of("sales"),
            "factory": Factory.objects.get(code="FA"),
            "buyer": "Example Buyer Ltd",
            "contract_number": "SC-000123",
            "product_name": "Sample Product",
            "delivery_date": datetime.date(2026, 10, 28),
            "packing_type": "bulk",
            "affected_quantity": Decimal("20"),
            "quantity_unit": "mt",
            "transport_type": "local_bulk",
            "vehicle_number": "WXY 1234",
            "loading_port": "Example Loading Port",
            "discharge_port": "Example Discharge Port",
            "description": "Seal damaged.",
            "complaint_type": ComplaintType.objects.get(name="Logistic"),
            "complaint_sub_type": ComplaintSubType.objects.get(name="Logistic - Shortage"),
            "sample_sent": False,
            "contact_email": "contact@example.com",
        }
        fields.update(overrides)
        return Complaint(**fields)

    return build


def test_a_complete_complaint_has_nothing_missing(complete_complaint):
    assert complete_complaint().missing_mandatory() == {}


def test_an_empty_draft_is_missing_every_mandatory_field(user_of):
    missing = Complaint(lodged_by=user_of("sales")).missing_mandatory()
    assert set(missing) == {
        "factory", "buyer", "contract_number", "product_name", "delivery_date", "packing_type",
        "affected_quantity", "quantity_unit", "transport_type", "loading_port", "discharge_port",
        "description", "complaint_type", "sample_sent", "contact_email",
    }  # fmt: skip


def test_sample_sent_no_counts_as_answered(complete_complaint):
    assert "sample_sent" not in complete_complaint(sample_sent=False).missing_mandatory()
    assert "sample_sent" in complete_complaint(sample_sent=None).missing_mandatory()


def test_optional_fields_are_not_mandatory(complete_complaint):
    complaint = complete_complaint(do_reference="", contact_person="", contact_phone="", contact_fax="")
    assert complaint.missing_mandatory() == {}


@pytest.mark.parametrize(
    "transport, needs",
    [
        ("shipment_bulk", {"vessel_name"}),
        ("shipment_container", {"vessel_name", "container_number"}),
        ("local_bulk", {"vehicle_number"}),
        ("local_container", {"vehicle_number", "container_number"}),
    ],
)
def test_transport_type_decides_which_fields_are_mandatory(complete_complaint, transport, needs):
    complaint = complete_complaint(
        transport_type=transport, vessel_name="", vehicle_number="", container_number=""
    )
    assert set(complaint.missing_mandatory()) == needs


def test_pack_size_is_mandatory_only_for_pack(complete_complaint):
    assert "pack_size" not in complete_complaint(packing_type="bulk").missing_mandatory()
    assert "pack_size" in complete_complaint(packing_type="pack", pack_size="").missing_mandatory()


def test_sub_type_is_mandatory_when_the_type_has_sub_types(complete_complaint):
    assert "complaint_sub_type" in complete_complaint(complaint_sub_type=None).missing_mandatory()


def test_others_needs_short_details_and_no_sub_type(complete_complaint):
    others = ComplaintType.objects.get(name="Others")
    complaint = complete_complaint(complaint_type=others, complaint_sub_type=None, type_details="")
    assert set(complaint.missing_mandatory()) == {"type_details"}
    complaint.type_details = "Customer disputes the invoice."
    assert complaint.missing_mandatory() == {}


def test_an_others_sub_type_needs_short_details(complete_complaint):
    sub_type = ComplaintSubType.objects.get(name="Logistic - Others")
    complaint = complete_complaint(complaint_sub_type=sub_type, type_details="")
    assert set(complaint.missing_mandatory()) == {"type_details"}


def test_inactive_sub_types_do_not_make_the_sub_type_mandatory(complete_complaint):
    others = ComplaintType.objects.get(name="Others")
    ComplaintSubType.objects.create(complaint_type=others, name="Retired", is_active=False)
    complaint = complete_complaint(complaint_type=others, complaint_sub_type=None, type_details="x")
    assert complaint.missing_mandatory() == {}


def test_saving_clears_fields_that_no_longer_apply(complete_complaint):
    complaint = complete_complaint(
        packing_type="bulk", pack_size="Drum", transport_type="local_bulk", vessel_name="MV Old",
        container_number="OLD123", type_details="old note",
    )  # fmt: skip
    complaint.assign_reference()
    complaint.save()
    complaint.refresh_from_db()
    assert (complaint.pack_size, complaint.vessel_name, complaint.container_number) == ("", "", "")
    assert complaint.vehicle_number == "WXY 1234"
    assert complaint.type_details == ""


def test_a_sub_type_of_another_type_is_dropped(complete_complaint):
    complaint = complete_complaint(complaint_type=ComplaintType.objects.get(name="Packaging"))
    complaint.assign_reference()
    complaint.save()
    assert complaint.complaint_sub_type is None


def test_submit_refuses_an_incomplete_draft(complete_complaint):
    complaint = complete_complaint(buyer="")
    complaint.assign_reference()
    complaint.save()
    with pytest.raises(ValidationError) as error:
        complaint.submit()
    assert "buyer" in error.value.message_dict
    complaint.refresh_from_db()
    assert complaint.is_draft


# --- Lodging through the form ------------------------------------------------------


def test_lodging_requires_sign_in(client, seeded):
    assert client.get(reverse("complaint_new")).status_code == 302


def test_form_page_shows_the_lodger_and_hides_nothing_server_side(as_user):
    response = as_user("sales").get(reverse("complaint_new"))
    assert response.status_code == 200
    html = response.content.decode()
    assert "Sample Sales" in html and "Sales" in html
    for name in ("pack_size", "vessel_name", "vehicle_number", "container_number", "type_details"):
        assert f'name="{name}"' in html  # present for the script to open, and for no-script use


def test_a_complete_form_is_submitted_and_the_coordinator_is_emailed(as_user, user_of):
    client = as_user("sales")
    response = lodge(client, submit="1")
    complaint = Complaint.objects.get()
    assert response.status_code == 302 and response.url == reverse("complaint_detail", args=[complaint.pk])
    assert complaint.status == Complaint.Status.SUBMITTED
    assert complaint.submitted_at is not None
    assert complaint.reference == f"FA/{datetime.date.today().year}/0001"
    assert (complaint.lodged_by, complaint.lodger_department) == (user_of("sales"), "Sales")

    assert len(mail.outbox) == 1
    email = mail.outbox[0]
    assert email.to == ["mr@example.com"]
    assert complaint.reference in email.subject
    assert reverse("complaint_detail", args=[complaint.pk]) in email.body
    assert "Example Buyer Ltd" in email.body and "Packaging / Packaging - Leaking" in email.body


def test_submission_still_works_when_the_email_cannot_be_sent(as_user, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("mail server down")

    monkeypatch.setattr("complaints.emails.EmailMessage.send", broken)
    response = lodge(as_user("sales"), submit="1")
    assert response.status_code == 302
    assert Complaint.objects.get().status == Complaint.Status.SUBMITTED


def test_submission_without_a_coordinator_warns_but_submits(as_user, user_of):
    from accounts.models import Role

    mr = user_of("mr")
    mr.role = Role.MANAGEMENT
    mr.save()
    client = as_user("sales")
    response = client.post(reverse("complaint_new"), complete_data(submit="1"), follow=True)
    assert Complaint.objects.get().status == Complaint.Status.SUBMITTED
    assert "could not be emailed" in response.content.decode()
    assert mail.outbox == []


def test_a_draft_needs_only_a_factory(as_user):
    client = as_user("sales")
    response = client.post(reverse("complaint_new"), {"factory": Factory.objects.get(code="FB").pk, "save": "1"})
    complaint = Complaint.objects.get()
    assert response.url == reverse("complaint_edit", args=[complaint.pk])
    assert complaint.is_draft and complaint.reference.startswith("FB/")
    assert mail.outbox == []


def test_a_draft_without_a_factory_is_refused(as_user):
    response = as_user("sales").post(reverse("complaint_new"), {"buyer": "Example Buyer Ltd", "save": "1"})
    assert response.status_code == 200
    assert "Choose the factory" in response.content.decode()
    assert Complaint.objects.count() == 0


def test_submitting_an_incomplete_form_keeps_a_draft_and_lists_what_is_missing(as_user):
    client = as_user("sales")
    response = lodge(client, submit="1", buyer="", contact_email="")
    assert response.status_code == 200
    html = response.content.decode()
    assert html.count("This field is required before the complaint can be submitted.") == 2
    assert "The draft is saved" in html
    complaint = Complaint.objects.get()
    assert complaint.is_draft and complaint.contract_number == "SC-000123"
    assert mail.outbox == []
    # The form now posts to the saved draft, so a second try cannot create a duplicate.
    assert reverse("complaint_edit", args=[complaint.pk]) in html
    fixed = client.post(reverse("complaint_edit", args=[complaint.pk]), complete_data(submit="1"))
    assert fixed.status_code == 302
    assert Complaint.objects.count() == 1
    assert Complaint.objects.get().status == Complaint.Status.SUBMITTED


def test_invalid_values_are_reported_even_for_a_draft(as_user):
    response = lodge(as_user("sales"), save="1", contact_email="not-an-email", affected_quantity="-5")
    html = response.content.decode()
    assert response.status_code == 200
    assert "Enter a valid email address." in html
    assert "greater than or equal to 0" in html
    assert Complaint.objects.count() == 0


def test_a_sub_type_must_belong_to_the_chosen_type(as_user):
    wrong = ComplaintSubType.objects.get(name="Logistic - Shortage").pk
    response = lodge(as_user("sales"), save="1", complaint_sub_type=wrong)
    assert "does not belong to the complaint type" in response.content.decode()
    assert Complaint.objects.count() == 0


def test_the_factory_cannot_change_once_the_reference_exists(as_user):
    client = as_user("sales")
    lodge(client, save="1")
    complaint = Complaint.objects.get()
    client.post(
        reverse("complaint_edit", args=[complaint.pk]),
        complete_data(factory=Factory.objects.get(code="FC").pk, save="1"),
    )
    complaint.refresh_from_db()
    assert complaint.factory.code == "FA" and complaint.reference.startswith("FA/")


def test_changing_a_draft_does_not_issue_a_new_reference(as_user):
    client = as_user("sales")
    lodge(client, save="1")
    complaint = Complaint.objects.get()
    reference = complaint.reference
    client.post(reverse("complaint_edit", args=[complaint.pk]), complete_data(buyer="Another Buyer", save="1"))
    complaint.refresh_from_db()
    assert (complaint.reference, complaint.buyer) == (reference, "Another Buyer")
    assert ReferenceCounter.objects.get().last_number == 1


def test_complaint_method_is_optional_and_comes_from_the_mr_list(as_user):
    method = ComplaintMethod.objects.get(name="Email")
    lodge(as_user("sales"), submit="1", complaint_method=method.pk)
    assert Complaint.objects.get().complaint_method == method


def test_changes_are_written_to_the_audit_log(as_user):
    from audit.models import AuditLog

    client = as_user("sales")
    lodge(client, save="1")
    complaint = Complaint.objects.get()
    client.post(reverse("complaint_edit", args=[complaint.pk]), complete_data(buyer="Another Buyer", save="1"))
    entries = AuditLog.objects.filter(object_repr=complaint.reference).order_by("id")
    assert [e.action for e in entries] == ["created", "updated"]
    assert entries[1].actor.username == "sales"
    assert entries[1].changes["buyer"][1] == "Another Buyer"


# --- Who can see and change what ---------------------------------------------------


def test_a_submitted_complaint_is_read_only(as_user):
    client = as_user("sales")
    lodge(client, submit="1")
    complaint = Complaint.objects.get()
    response = client.post(reverse("complaint_edit", args=[complaint.pk]), complete_data(buyer="Changed", save="1"))
    assert response.status_code == 302 and response.url == reverse("complaint_detail", args=[complaint.pk])
    complaint.refresh_from_db()
    assert complaint.buyer == "Example Buyer Ltd"


def test_only_the_lodger_sees_a_draft(as_user, user_of):
    lodge(as_user("sales"), save="1")
    complaint = Complaint.objects.get()
    for username in ("mr", "management", "fa.pic", "fa.hod", "fa.fm"):
        client = as_user(username)
        assert client.get(reverse("complaint_detail", args=[complaint.pk])).status_code == 404
        assert complaint.reference not in client.get(reverse("home")).content.decode()
        client.logout()
    assert as_user("sales").get(reverse("complaint_detail", args=[complaint.pk])).status_code == 200


def test_only_the_lodger_can_edit_their_draft(as_user):
    lodge(as_user("sales"), save="1")
    complaint = Complaint.objects.get()
    assert as_user("sales").get(reverse("complaint_edit", args=[complaint.pk])).status_code == 200
    assert as_user("fa.pic").get(reverse("complaint_edit", args=[complaint.pk])).status_code == 404


def test_submitted_complaints_are_visible_by_role_and_factory(as_user):
    lodge(as_user("sales"), submit="1")  # Factory A
    complaint = Complaint.objects.get()
    url = reverse("complaint_detail", args=[complaint.pk])
    expected = {
        "mr": 200, "management": 200, "sales": 200,
        "fa.pic": 200, "fa.lab": 200, "fa.hod": 200, "fa.fm": 200,
        "fb.pic": 404, "fb.hod": 404, "fe.fm": 404,
    }  # fmt: skip
    for username, status in expected.items():
        client = as_user(username)
        assert client.get(url).status_code == status, username
        client.logout()


def test_a_factory_user_sees_a_complaint_they_lodged_in_another_factory(as_user):
    lodge(as_user("fb.pic"), submit="1")  # lodged by a Factory B user for Factory A
    complaint = Complaint.objects.get()
    assert as_user("fb.pic").get(reverse("complaint_detail", args=[complaint.pk])).status_code == 200


def test_home_lists_only_what_the_user_may_see(as_user):
    lodge(as_user("sales"), submit="1")
    lodge(as_user("sales"), submit="1", factory=Factory.objects.get(code="FB").pk)
    pages = {}
    for username in ("mr", "fa.pic", "fb.pic", "fc.pic", "sales"):
        client = as_user(username)
        pages[username] = client.get(reverse("home")).content.decode()
        client.logout()
    year = datetime.date.today().year
    assert f"FA/{year}/0001" in pages["mr"] and f"FB/{year}/0001" in pages["mr"]
    assert f"FA/{year}/0001" in pages["fa.pic"] and f"FB/{year}/0001" not in pages["fa.pic"]
    assert f"FB/{year}/0001" in pages["fb.pic"] and f"FA/{year}/0001" not in pages["fb.pic"]
    assert "No complaints yet" in pages["fc.pic"]
    assert f"FA/{year}/0001" in pages["sales"] and f"FB/{year}/0001" in pages["sales"]


# --- Attachments -------------------------------------------------------------------


def upload(name="photo.png", content=b"fake image bytes", size=None):
    return SimpleUploadedFile(name, content if size is None else b"x" * size)


def test_files_are_stored_listed_and_downloaded(as_user):
    client = as_user("sales")
    lodge(client, save="1", attachments=[upload("leak photo.png"), upload("report.pdf", b"%PDF-1.4")])
    complaint = Complaint.objects.get()
    assert [a.original_name for a in complaint.attachments.all()] == ["leak photo.png", "report.pdf"]
    first = complaint.attachments.first()
    assert "leak photo" not in first.file.name  # stored under a random name
    response = client.get(reverse("attachment_download", args=[first.pk]))
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"fake image bytes"
    assert "attachment" in response["Content-Disposition"] and "leak photo.png" in response["Content-Disposition"]
    assert "leak photo.png" in client.get(reverse("complaint_edit", args=[complaint.pk])).content.decode()


def test_files_of_an_unaccepted_type_are_refused(as_user):
    response = lodge(as_user("sales"), save="1", attachments=[upload("virus.exe")])
    assert "files of type .exe are not accepted" in response.content.decode()
    assert Complaint.objects.count() == 0


def test_files_over_the_size_limit_are_refused(as_user, settings):
    settings.ATTACHMENT_MAX_MB = 1
    response = lodge(as_user("sales"), save="1", attachments=[upload("big.pdf", size=1024 * 1024 + 1)])
    assert "larger than 1 MB" in response.content.decode()
    assert Attachment.objects.count() == 0


def test_attachments_follow_the_complaint_visibility(as_user):
    lodge(as_user("sales"), save="1", attachments=[upload()])
    attachment = Attachment.objects.get()
    url = reverse("attachment_download", args=[attachment.pk])
    assert as_user("mr").get(url).status_code == 404  # still a draft
    complaint = Complaint.objects.get()
    complaint.submit()
    expected = {"mr": 200, "fa.pic": 200, "fb.pic": 404}
    for username, status in expected.items():
        client = as_user(username)
        assert client.get(url).status_code == status, username
        client.logout()
    client = as_user("sales")
    client.logout()
    assert client.get(url).status_code == 302  # not signed in


def test_a_file_can_be_removed_from_a_draft_only(as_user):
    client = as_user("sales")
    lodge(client, save="1", attachments=[upload("a.png"), upload("b.png")])
    complaint = Complaint.objects.get()
    first, second = complaint.attachments.all()
    stored = first.file.path
    client.post(reverse("attachment_delete", args=[first.pk]))
    assert list(complaint.attachments.all()) == [second]
    import os

    assert not os.path.exists(stored)

    complaint.submit()
    response = client.post(reverse("attachment_delete", args=[second.pk]))
    assert response.status_code == 302
    assert complaint.attachments.count() == 1
    assert client.get(reverse("attachment_delete", args=[second.pk])).status_code == 405


def test_another_user_cannot_remove_a_file(as_user):
    lodge(as_user("sales"), save="1", attachments=[upload()])
    attachment = Attachment.objects.get()
    assert as_user("fa.pic").post(reverse("attachment_delete", args=[attachment.pk])).status_code == 404
    assert Attachment.objects.count() == 1
