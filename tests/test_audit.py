import pytest
from django.contrib.auth.models import AnonymousUser

from accounts.models import Factory
from audit.context import get_actor, reset_actor, set_actor
from audit.models import AuditLog
from complaints.models import ComplaintType


def test_create_update_delete_are_logged_with_the_actor(user_of):
    mr = user_of("mr")
    token = set_actor(mr)
    try:
        ctype = ComplaintType.objects.create(name="Pricing", reminder_days=5)
        ctype.reminder_days = 7
        ctype.save()
        ctype.save()  # no change, no entry
        pk = ctype.pk
        ctype.delete()
    finally:
        reset_actor(token)

    entries = list(AuditLog.objects.filter(object_repr="Pricing").order_by("id"))
    assert [e.action for e in entries] == ["created", "updated", "deleted"]
    assert all(e.actor == mr and e.actor_name == "Sample Coordinator" for e in entries)
    assert entries[1].changes == {"reminder_days": [5, 7]}
    assert entries[2].object_id == str(pk)


def test_changes_outside_a_request_have_no_actor(seeded):
    fa = Factory.objects.get(code="FA")
    fa.name = "Factory Alpha"
    fa.save()
    entry = AuditLog.objects.get(object_repr="Factory Alpha")
    assert entry.actor is None
    assert entry.changes == {"name": ["Factory A", "Factory Alpha"]}


def test_anonymous_requests_leave_no_actor():
    token = set_actor(AnonymousUser())
    try:
        assert get_actor() is None
    finally:
        reset_actor(token)


def test_passwords_never_appear_in_the_log(user_of):
    user = user_of("sales")
    user.set_password("another-pass-456")
    user.save()
    entries = AuditLog.objects.filter(object_repr=str(user))
    assert entries.exists()  # the account's creation was logged
    for entry in entries:
        assert "password" not in entry.changes
        assert "last_login" not in entry.changes


def test_uploaded_files_are_logged_by_stored_name(user_of):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from complaints.models import Attachment, Complaint

    sales = user_of("sales")
    complaint = Complaint.objects.create(lodged_by=sales, factory=Factory.objects.get(code="FA"))
    attachment = Attachment.objects.create(
        complaint=complaint, file=SimpleUploadedFile("photo.png", b"x"), original_name="photo.png", uploaded_by=sales
    )
    entry = AuditLog.objects.get(object_repr="photo.png", action="created")
    assert entry.changes["file"][1] == attachment.file.name
    assert entry.changes["file"][1].startswith(f"attachments/{complaint.pk}/")


def test_entries_are_append_only(user_of):
    entry = AuditLog.objects.first()
    with pytest.raises(PermissionError):
        entry.save()
    with pytest.raises(PermissionError):
        entry.delete()
