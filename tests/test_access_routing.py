"""Feedback of the demo: access by factory and entity, and auto assign by the routing table."""

import re

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse

from accounts.access import factories_for, scope_to_user
from accounts.models import Department, Factory, FactoryPIC, Role, User
from complaints import people, workflow
from complaints.models import Complaint, ComplaintSubType, ComplaintType, NotificationLog, RoutingRule
from tests.test_complaints import as_user, complete_data, days_ago, lodge  # noqa: F401  (fixtures and helpers)
from tests.test_workflow import fresh, submitted  # noqa: F401


def fb():
    return Factory.objects.get(code="FB").pk


def visible_references(user):
    return {c.factory.code for c in Complaint.objects.visible_to(user)}


# --- Access control and visibility (the example table of the feedback) ----------------------------------------------------------


@pytest.fixture
def complaints_everywhere(as_user):
    """One submitted complaint in every factory, lodged by someone outside the factories."""
    sales = as_user("sales")
    for factory in Factory.objects.all():
        lodge(sales, submit="1", factory=factory.pk)
    return Complaint.objects.all()


def test_a_factory_a_qa_executive_sees_factory_a_only(complaints_everywhere, user_of):
    qa = user_of("fa.lab")
    assert qa.dept.name == "QA" and visible_references(qa) == {"FA"}
    assert [f.code for f in factories_for(qa)] == ["FA"]


def test_a_factory_b_production_manager_sees_factory_b_only(complaints_everywhere, user_of):
    assert visible_references(user_of("fb.hod")) == {"FB"}
    assert visible_references(user_of("fb.pic")) == {"FB"}


def test_a_trader_handling_factories_a_and_b_sees_both_and_no_other(complaints_everywhere, user_of):
    alan = user_of("alan.trader")
    assert alan.role == Role.TRADER and alan.factory_id is None
    assert visible_references(alan) == {"FA", "FB"}
    assert sorted(f.code for f in factories_for(alan)) == ["FA", "FB"]


def test_a_group_shipping_pic_sees_every_factory_assigned_to_them(complaints_everywhere, user_of):
    group = user_of("group.shipping")
    assert group.dept.name == "Shipping" and visible_references(group) == {"FA", "FB", "FC", "FD", "FE"}
    group.assigned_factories.remove(Factory.objects.get(code="FE"), Factory.objects.get(code="FD"), Factory.objects.get(code="FC"))
    assert visible_references(group) == {"FA", "FB"}  # only the assigned entities


def test_the_coordinator_and_management_see_all_factories(complaints_everywhere, user_of):
    for username in ("mr", "management"):
        assert visible_references(user_of(username)) == {"FA", "FB", "FC", "FD", "FE"}
        assert factories_for(user_of(username)).count() == 5


def test_a_user_with_no_factory_assigned_sees_nothing_of_the_factories(complaints_everywhere, user_of):
    lone = User.objects.create(username="lone.trader", email="lone@example.com", role=Role.TRADER)
    assert visible_references(lone) == set() and factories_for(lone).count() == 0


def test_changing_the_assigned_factories_changes_what_a_user_sees_at_once(complaints_everywhere, user_of):
    alan = user_of("alan.trader")
    alan.assigned_factories.add(Factory.objects.get(code="FC"))
    assert visible_references(alan) == {"FA", "FB", "FC"}
    alan.assigned_factories.clear()
    assert visible_references(alan) == set()


def test_a_trader_who_covers_a_factory_can_open_its_complaints_but_not_change_them(complaints_everywhere, as_user, user_of):
    complaint = Complaint.objects.get(factory__code="FB")
    client = as_user("alan.trader")
    assert client.get(reverse("complaint_detail", args=[complaint.pk])).status_code == 200
    assert workflow.available_actions(user_of("alan.trader"), complaint) == []
    other = Complaint.objects.get(factory__code="FC")
    assert client.get(reverse("complaint_detail", args=[other.pk])).status_code == 404


def test_the_dashboard_and_the_log_follow_the_factories_a_user_covers(complaints_everywhere, as_user):
    for username, expected in (("alan.trader", 2), ("group.shipping", 5), ("fa.lab", 1), ("mr", 5)):
        html = as_user(username).get(reverse("dashboard")).content.decode()
        assert f"<b>{expected}</b><span>Total number of complaints" in html, username
        log = as_user(username).get(reverse("complaint_log")).content.decode()
        assert log.count('<td><a href="/complaints/') == expected, username


def test_the_factory_choices_of_the_filters_are_the_factories_covered(complaints_everywhere, as_user):
    html = as_user("alan.trader").get(reverse("dashboard")).content.decode().split('class="chips"')[1].split("</form>")[0]
    assert "Factory A" in html and "Factory B" in html and "Factory C" not in html


def test_the_excel_export_of_a_trader_holds_only_their_factories(complaints_everywhere, as_user):
    import io

    from openpyxl import load_workbook

    sheet = load_workbook(io.BytesIO(as_user("alan.trader").get(reverse("log_export")).content))["Complaint log"]
    values = [sheet.cell(row=r, column=2).value for r in range(2, sheet.max_row + 1)]
    refs = {v[:2] for v in values if isinstance(v, str) and re.match(r"F[A-E]/\d{4}/\d{4}$", v)}
    assert refs == {"FA", "FB"}  # the two complaints of the factories Alan covers, and nothing of the others


def test_a_trader_can_lodge_a_complaint_for_a_factory(as_user):
    response = lodge(as_user("alan.trader"), submit="1", factory=fb())
    assert response.status_code == 302 and Complaint.objects.get().lodged_by.username == "alan.trader"


def test_a_factory_user_cannot_see_a_complaint_of_another_factory_by_its_address(complaints_everywhere, as_user):
    other = Complaint.objects.get(factory__code="FB")
    assert as_user("fa.hod").get(reverse("complaint_detail", args=[other.pk])).status_code == 404
    assert as_user("fa.hod").get(reverse("complaint_pdf", args=[other.pk])).status_code == 404


def test_scope_to_user_works_on_any_queryset_of_factory_records(complaints_everywhere, user_of):
    assert scope_to_user(Complaint.objects.all(), user_of("alan.trader")).count() == 2


def test_a_pic_can_only_be_listed_for_a_factory_the_user_covers(user_of):
    group = user_of("group.shipping")
    FactoryPIC(factory=Factory.objects.get(code="FC"), user=group, designation="Group Shipping PIC").clean()  # covers it
    lone = User.objects.create(username="fa.extra", email="x@example.com", role=Role.PIC, factory=Factory.objects.get(code="FA"))
    with pytest.raises(ValidationError):
        FactoryPIC(factory=Factory.objects.get(code="FB"), user=lone, designation="PIC").clean()


def test_a_new_factory_user_needs_a_factory_but_one_with_assigned_factories_does_not(db):
    new = User(username="x", email="x@example.com", role=Role.HOD)
    with pytest.raises(ValidationError):
        new.full_clean(exclude=["password"])
    existing = User.objects.create(username="y", email="y@example.com", role=Role.HOD, factory=Factory.objects.create(code="ZZ", name="Z"))
    existing.assigned_factories.add(existing.factory)
    existing.factory = None
    existing.full_clean(exclude=["password"])  # covers a factory through the assigned list


def test_the_administration_panel_maintains_the_access_rights(client, user_of, seeded):
    from django.test import Client

    User.objects.create_superuser("admin", "admin@example.com", "admin-pass-12345")
    admin = Client()
    assert admin.login(username="admin", password="admin-pass-12345")
    alan = user_of("alan.trader")
    html = admin.get(reverse("admin:accounts_user_change", args=[alan.pk])).content.decode()
    assert "assigned_factories" in html and "Department (for routing)" in html
    response = admin.post(reverse("admin:accounts_user_changelist"), {"action": "x"})  # the list page renders with the new columns
    listing = admin.get(reverse("admin:accounts_user_changelist")).content.decode()
    assert "Factories covered" in listing
    # Only an administrator can open it: the Coordinator, who is staff, cannot edit users.
    coordinator = Client()
    coordinator.login(username="mr", password="demo-pass-123")
    assert coordinator.get(reverse("admin:accounts_user_changelist")).status_code == 403


# --- The routing table ------------------------------------------------------------------------------------------------------------------


def routed(**overrides):
    return Complaint(factory=Factory.objects.get(code="FA"), **overrides)


def kind(type_name, sub_type=None):
    return {"complaint_type": ComplaintType.objects.get(name=type_name)}


@pytest.mark.parametrize(
    "type_name, transport, department",
    [
        ("Product Quality", "shipment_bulk", "QA"),
        ("Product Quality", "local_bulk", "QA"),
        ("Packaging", "shipment_container", "Production"),
        ("Packaging", "local_container", "Production"),
        ("Logistic", "local_bulk", "Operation"),
        ("Logistic", "local_container", "Operation"),
        ("Logistic", "shipment_bulk", "Shipping"),
        ("Logistic", "shipment_container", "Shipping"),
        ("Documentation", "local_bulk", "Operation"),
        ("Documentation", "shipment_container", "Shipping"),
        ("Others", "local_bulk", None),
        ("Others", "shipment_bulk", None),
    ],
)
def test_the_routing_table_of_the_feedback(seeded, type_name, transport, department):
    complaint = routed(complaint_type=ComplaintType.objects.get(name=type_name), transport_type=transport)
    found = RoutingRule.department_for(complaint)
    assert (found.name if found else None) == department


def test_a_complaint_with_no_type_or_transport_is_not_routed(seeded):
    assert RoutingRule.department_for(routed()) is None
    assert RoutingRule.department_for(routed(complaint_type=ComplaintType.objects.get(name="Logistic"))) is None  # needs local or shipment


def test_the_coordinator_can_change_the_routing_table(seeded):
    rule = RoutingRule.objects.get(complaint_type__name="Packaging")
    rule.department = Department.objects.get(name="QA")
    rule.save()
    assert RoutingRule.department_for(routed(complaint_type=rule.complaint_type, transport_type="local_bulk")).name == "QA"
    # A rule for any transport is used when there is none for the kind of transport.
    RoutingRule.objects.create(complaint_type=ComplaintType.objects.get(name="Others"), scope="any", department=Department.objects.get(name="Shipping"))
    assert RoutingRule.department_for(routed(complaint_type=ComplaintType.objects.get(name="Others"), transport_type="local_bulk")).name == "Shipping"


def test_the_default_departments_and_rules_are_loaded_by_seed(db):
    call_command("seed", verbosity=0)  # reference data only, no demo users
    assert list(Department.objects.values_list("name", flat=True)) == ["QA", "Production", "Operation", "Shipping"]
    assert RoutingRule.objects.count() == 6
    assert Department.objects.count() == 4
    call_command("seed", verbosity=0)
    assert RoutingRule.objects.count() == 6  # safe to run again


@pytest.fixture
def auto(settings):
    settings.AUTO_ASSIGN = True


def lodge_for(as_user, type_name, sub_type, transport="local_bulk", **extra):
    data = {
        "complaint_type": ComplaintType.objects.get(name=type_name).pk,
        "complaint_sub_type": ComplaintSubType.objects.get(name=sub_type).pk,
        "transport_type": transport,
    }
    if transport.startswith("local"):
        data.update(vehicle_number="WXY 1234", delivery_date=days_ago(5), vessel_name="")
    data.update(extra)
    return lodge(as_user("sales"), submit="1", **data)


def test_a_packaging_complaint_goes_straight_to_the_production_pic_with_the_coordinator_copied(auto, as_user):
    mail.outbox.clear()
    response = lodge(as_user("sales"), submit="1")  # Packaging at Factory A
    complaint = Complaint.objects.get()
    assert response.status_code == 302
    assert complaint.status == Complaint.Status.INVESTIGATION and complaint.department.name == "Production"
    assert [a.user.username for a in complaint.assignments.all()] == ["fa.pic"]
    assert complaint.acknowledged_at is not None and complaint.received_by is None  # nobody at the desk: the table did it
    (email,) = mail.outbox  # one email: the PIC, with the Coordinator copied; the Coordinator is not asked to assign
    assert email.to == ["fa.pic@example.com"] and email.cc == ["mr@example.com"]
    assert "assigned to you automatically" in email.body and "Production department" in email.body
    assert email.attachments[0][0].endswith("summary.pdf")
    event = complaint.events.get(action="assigned")
    assert event.actor is None and "Assigned automatically to Production" in event.comment


def test_the_lodger_is_told_where_it_went(auto, as_user):
    response = as_user("sales").post(reverse("complaint_new"), complete_data(submit="1"), follow=True)
    html = response.content.decode()
    assert "assigned to the Production department" in html and "Sample PIC FA" in html and "The Coordinator is copied" in html


def test_a_quality_complaint_goes_to_the_qa_pics(auto, as_user):
    lodge(as_user("sales"), submit="1", complaint_type=ComplaintType.objects.get(name="Product Quality").pk,
          complaint_sub_type=ComplaintSubType.objects.get(name="Quality - Off Spec").pk)  # fmt: skip
    complaint = Complaint.objects.get()
    assert complaint.department.name == "QA"
    assert {a.user.username for a in complaint.assignments.all()} == {"fa.lab", "fa.labmgr"}


def test_a_logistic_complaint_goes_to_operation_or_shipping_by_the_transport(auto, as_user):
    lodge_for(as_user, "Logistic", "Logistic - Shortage", "local_bulk")
    lodge_for(as_user, "Logistic", "Logistic - Shortage", "shipment_bulk", vessel_name="MV Example")
    local, shipment = Complaint.objects.order_by("id")
    assert local.department.name == "Operation" and [a.user.username for a in local.assignments.all()] == ["fa.oppic"]
    assert shipment.department.name == "Shipping"
    assert {a.user.username for a in shipment.assignments.all()} == {"fa.shippic", "group.shipping"}  # the group PIC covers every factory


def test_a_documentation_complaint_follows_the_same_split(auto, as_user):
    lodge_for(as_user, "Documentation", "Documentation - Missing", "local_container", container_number="ABCD1234567")
    assert Complaint.objects.get().department.name == "Operation"


def test_others_goes_to_the_coordinator_who_is_asked_to_assign_it(auto, as_user):
    mail.outbox.clear()
    lodge(as_user("sales"), submit="1", complaint_type=ComplaintType.objects.get(name="Others").pk,
          complaint_sub_type="", type_details="Customer disputes the invoice")  # fmt: skip
    complaint = Complaint.objects.get()
    assert complaint.status == Complaint.Status.SUBMITTED and complaint.department is None and not complaint.assignments.exists()
    (email,) = mail.outbox
    assert email.to == ["mr@example.com"] and "waiting for you to assign" in email.body


def test_a_department_with_no_pic_at_the_factory_stays_with_the_coordinator(auto, as_user):
    FactoryPIC.objects.filter(factory__code="FA", department__name="Production").delete()
    mail.outbox.clear()
    lodge(as_user("sales"), submit="1")
    assert Complaint.objects.get().status == Complaint.Status.SUBMITTED
    assert mail.outbox[0].to == ["mr@example.com"]


def test_auto_assign_can_be_switched_off(settings, as_user):
    settings.AUTO_ASSIGN = False
    lodge(as_user("sales"), submit="1")
    assert Complaint.objects.get().status == Complaint.Status.SUBMITTED


def test_a_complaint_is_assigned_only_once(auto, as_user):
    lodge(as_user("sales"), submit="1")
    complaint = Complaint.objects.get()
    assert workflow.auto_assign(fresh(complaint)) is None  # it is no longer waiting for the Coordinator
    assert complaint.assignments.count() == 1


def test_the_pics_log_line_and_the_coordinator_copy_are_recorded(auto, as_user):
    lodge(as_user("sales"), submit="1")
    line = NotificationLog.objects.get(kind="assigned")
    assert line.to_addresses == "fa.pic@example.com" and line.cc_addresses == "mr@example.com" and line.success


def test_the_coordinator_can_reassign_an_automatic_assignment(auto, as_user, user_of):
    lodge(as_user("sales"), submit="1")  # Production
    complaint = Complaint.objects.get()
    entries = list(FactoryPIC.objects.filter(factory__code="FA", user__username="fa.lab"))
    assert "complaint_assign" in [a["url_name"] for a in workflow.available_actions(user_of("mr"), complaint)]
    workflow.assign(complaint, user_of("mr"), entries, "Belongs to QA", department=Department.objects.get(name="QA"))
    complaint = fresh(complaint)
    assert [a.user.username for a in complaint.assignments.all()] == ["fa.lab"] and complaint.department.name == "QA"
    assert complaint.status == Complaint.Status.INVESTIGATION  # a change of PIC does not restart the process


def test_the_assign_page_suggests_the_routed_department(as_user, user_of):
    complaint = submitted(as_user)  # Packaging, auto assign off
    html = as_user("mr").get(reverse("complaint_assign", args=[complaint.pk])).content.decode()
    assert 'name="department"' in html and '<option value="%d" selected>Production</option>' % Department.objects.get(name="Production").pk in html
    assert "Production PIC (Production): Sample PIC FA" in html


# --- The HOD of the department reviews -----------------------------------------------------------------------------------------------


def to_hod_review(as_user, user_of, type_name="Product Quality", sub_type="Quality - Off Spec"):
    lodge(as_user("sales"), submit="1", complaint_type=ComplaintType.objects.get(name=type_name).pk,
          complaint_sub_type=ComplaintSubType.objects.get(name=sub_type).pk)  # fmt: skip
    complaint = Complaint.objects.latest("id")
    pic = user_of("fa.labmgr")
    entries = list(FactoryPIC.objects.filter(factory__code="FA", user=pic))
    workflow.assign(complaint, user_of("mr"), entries, "")
    workflow.save_investigation(fresh(complaint), pic, "Cause", "Fix", "Prevent", "internal")
    mail.outbox.clear()
    workflow.submit_to_hod(fresh(complaint), pic)
    return fresh(complaint)


def test_only_the_hod_of_the_complaints_department_reviews_it(as_user, user_of):
    complaint = to_hod_review(as_user, user_of)  # a quality complaint: QA
    assert complaint.department.name == "QA"
    assert [m.to for m in mail.outbox] == [["fa.qahod@example.com"]]
    assert "complaint_hod" in [a["url_name"] for a in workflow.available_actions(user_of("fa.qahod"), complaint)]
    for other in ("fa.hod", "fa.ophod", "fa.shiphod", "fb.qahod"):  # another department, or another factory
        assert "complaint_hod" not in [a["url_name"] for a in workflow.available_actions(user_of(other), complaint)], other
        assert as_user(other).get(reverse("complaint_hod", args=[complaint.pk])).status_code in (302, 404)
    assert people.hods(complaint) == [user_of("fa.qahod")]


def test_waiting_for_me_counts_only_the_hods_own_department(as_user, user_of):
    complaint = to_hod_review(as_user, user_of)
    def waiting(username):
        user = user_of(username)
        return list(Complaint.objects.visible_to(user).filter(workflow.waiting_for_me(user)))

    assert waiting("fa.qahod") == [complaint] and waiting("fa.hod") == [] and waiting("fa.ophod") == []


def test_the_fm_is_the_factorys_fm_whatever_the_department(as_user, user_of):
    complaint = to_hod_review(as_user, user_of)
    workflow.hod_submit_to_fm(complaint, user_of("fa.qahod"), "valid", "internal")
    assert [m.to for m in mail.outbox][-1] == ["fa.fm@example.com"]
    assert "complaint_fm" in [a["url_name"] for a in workflow.available_actions(user_of("fa.fm"), fresh(complaint))]


def test_without_a_department_every_hod_of_the_factory_may_review(as_user, user_of):
    complaint = submitted(as_user)
    Complaint.objects.filter(pk=complaint.pk).update(department=None)
    assert {u.username for u in people.hod_users(fresh(complaint))} == {"fa.hod", "fa.qahod", "fa.ophod", "fa.shiphod"}


def test_a_department_with_no_hod_at_the_factory_falls_back_to_all_of_them(as_user, user_of):
    complaint = to_hod_review(as_user, user_of)
    User.objects.filter(username="fa.qahod").update(dept=None)  # no HOD is left in QA at Factory A
    assert {u.username for u in people.hod_users(fresh(complaint))} == {"fa.hod", "fa.qahod", "fa.ophod", "fa.shiphod"}


def test_a_deputy_acts_for_the_department_hod_who_is_away(as_user, user_of):
    import datetime

    complaint = to_hod_review(as_user, user_of)
    hod, deputy = user_of("fa.qahod"), user_of("fa.ophod")
    hod.deputy, hod.away_until = deputy, datetime.date.today() + datetime.timedelta(days=3)
    hod.save()
    assert people.can_act_as_hod(deputy, complaint) and people.hods(complaint) == [deputy]


def test_the_assignment_moves_the_review_when_the_coordinator_changes_the_department(as_user, user_of):
    complaint = to_hod_review(as_user, user_of)  # QA
    workflow.hod_return(complaint, user_of("fa.qahod"), "Please improve")
    entries = list(FactoryPIC.objects.filter(factory__code="FA", user__username="fa.pic"))
    workflow.assign(fresh(complaint), user_of("mr"), entries, "", department=Department.objects.get(name="Production"))
    assert [h.username for h in people.hod_users(fresh(complaint))] == ["fa.hod"]
