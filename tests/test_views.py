from django.urls import reverse

from audit.models import AuditLog
from complaints.models import LabTest


def test_home_requires_sign_in(client, seeded):
    response = client.get(reverse("home"))
    assert response.status_code == 302
    assert response.url.startswith(reverse("login"))


def test_sign_in_and_home(client, seeded):
    assert client.login(username="fa.pic", password="demo-pass-123")
    response = client.get(reverse("home"))
    assert response.status_code == 200
    assert b"Factory A" in response.content


def test_admin_is_for_the_coordinator_only(client, seeded):
    client.login(username="fa.pic", password="demo-pass-123")
    assert client.get(reverse("admin:index")).status_code == 302
    client.logout()
    client.login(username="mr", password="demo-pass-123")
    assert client.get(reverse("admin:complaints_complainttype_changelist")).status_code == 200
    assert client.get(reverse("admin:accounts_user_changelist")).status_code == 403


def test_actor_is_recorded_for_admin_edits(client, seeded):
    client.login(username="mr", password="demo-pass-123")
    test = LabTest.objects.get(name="PV")
    response = client.post(
        reverse("admin:complaints_labtest_change", args=[test.pk]),
        {"name": "Peroxide value", "sort_order": test.sort_order, "is_active": "on"},
    )
    assert response.status_code == 302
    entry = AuditLog.objects.get(object_repr="Peroxide value")
    assert entry.actor.username == "mr"
    assert entry.changes == {"name": ["PV", "Peroxide value"]}
