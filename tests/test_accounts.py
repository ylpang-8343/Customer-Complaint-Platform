import datetime

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command

from accounts.access import factories_for, scope_to_user
from accounts.models import COORDINATOR_GROUP, Factory, FactoryPIC, Role, User
from complaints.models import ComplaintSubType, ComplaintType, LabTest


def test_seed_creates_five_factories_and_types(seeded):
    assert list(Factory.objects.values_list("code", flat=True)) == ["FA", "FB", "FC", "FD", "FE"]
    assert ComplaintType.objects.count() == 5
    assert ComplaintSubType.objects.count() == 16
    assert ComplaintType.objects.get(name="Others").requires_details
    assert ComplaintSubType.objects.get(name="Quality - Others").requires_details
    assert not ComplaintSubType.objects.get(name="Quality - Off Spec").requires_details
    assert LabTest.objects.count() == 6
    assert ComplaintType.objects.get(name="Packaging").reminder_days == 3


def test_seed_is_idempotent(seeded):
    call_command("seed", "--demo", verbosity=0)
    assert Factory.objects.count() == 5
    assert User.objects.filter(username="mr").count() == 1


def test_only_the_coordinator_is_staff_and_gets_settings_permissions(user_of):
    mr = user_of("mr")
    assert mr.is_staff
    assert mr.groups.filter(name=COORDINATOR_GROUP).exists()
    assert mr.has_perm("complaints.change_complainttype")
    assert not mr.has_perm("accounts.add_user")
    assert not user_of("fa.pic").is_staff


def test_changing_role_removes_staff_access(user_of):
    mr = user_of("mr")
    mr.role = Role.MANAGEMENT
    mr.save()
    mr.refresh_from_db()
    assert not mr.is_staff
    assert not mr.groups.filter(name=COORDINATOR_GROUP).exists()


def test_factory_roles_need_a_factory_and_group_roles_must_not_have_one(seeded):
    fa = Factory.objects.get(code="FA")
    with pytest.raises(ValidationError):
        User(username="x", email="x@example.com", role=Role.HOD).full_clean(exclude=["password"])
    with pytest.raises(ValidationError):
        User(username="y", email="y@example.com", role=Role.COORDINATOR, factory=fa).full_clean(
            exclude=["password"]
        )


def test_email_is_unique(seeded):
    with pytest.raises(ValidationError):
        User(username="dup", email="mr@example.com", role=Role.LODGER).full_clean(exclude=["password"])


def test_pic_must_belong_to_the_factory(user_of):
    fb = Factory.objects.get(code="FB")
    entry = FactoryPIC(factory=fb, user=user_of("fa.pic"), designation="Production PIC")
    with pytest.raises(ValidationError):
        entry.full_clean()


def test_deputy_acts_only_while_the_user_is_away(user_of):
    fm, hod = user_of("fa.fm"), user_of("fa.hod")
    fm.deputy = hod
    fm.away_until = datetime.date(2026, 11, 10)
    fm.save()
    assert fm.acting_user(datetime.date(2026, 11, 10)) == hod
    assert fm.acting_user(datetime.date(2026, 11, 11)) == fm
    hod.is_active = False
    hod.save()
    fm.refresh_from_db()
    assert fm.acting_user(datetime.date(2026, 11, 9)) == fm


def test_factories_visible_to_each_role(user_of):
    assert factories_for(user_of("mr")).count() == 5
    assert factories_for(user_of("management")).count() == 5
    assert list(factories_for(user_of("fb.hod")).values_list("code", flat=True)) == ["FB"]
    assert factories_for(user_of("sales")).count() == 0


def test_scope_to_user(user_of):
    # The complaint model arrives in the next step. Until then users stand in for it:
    # each belongs to a factory, and "lodged by" is read from the deputy link.
    lodger, fb_hod = user_of("sales"), user_of("fb.hod")
    fb_hod.deputy = lodger
    fb_hod.save()
    qs = User.objects.all()

    def scoped(user):
        return scope_to_user(qs, user, factory_field="factory", lodger_field="deputy")

    assert scoped(user_of("mr")).count() == qs.count()
    assert scoped(user_of("management")).count() == qs.count()
    # Factory staff see their own factory, and nothing of another factory.
    assert set(scoped(user_of("fa.pic")).values_list("factory__code", flat=True)) == {"FA"}
    # A lodger sees only what they lodged, wherever it belongs.
    assert list(scoped(lodger)) == [fb_hod]
    # Factory staff also see what they lodged in another factory.
    fb_hod.deputy = user_of("fa.pic")
    fb_hod.save()
    assert fb_hod in scoped(user_of("fa.pic"))
    assert fb_hod not in scoped(user_of("fc.pic"))
