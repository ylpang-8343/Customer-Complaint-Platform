"""A complaint's department (whose HOD reviews it) must be the department of the PICs who investigate.

Before this, a Coordinator who changed the PICs and left the Department as it was could leave a Production PIC's complaint
with the QA HOD. Open complaints are put right: the department becomes that of the investigating (not laboratory) PICs.
"""

from django.db import migrations

OPEN = ["investigation", "pending_scar", "hod_review", "fm_review", "approved", "response_sent"]


def follow_the_pics(apps, schema_editor):
    Complaint = apps.get_model("complaints", "Complaint")
    FactoryPIC = apps.get_model("accounts", "FactoryPIC")
    for complaint in Complaint.objects.filter(status__in=OPEN, deleted_at__isnull=True):
        users = list(complaint.assignments.select_related("user"))
        investigators = [a.user_id for a in users if a.user.role != "lab"] or [a.user_id for a in users]
        departments = []
        for user_id in investigators:
            entry = FactoryPIC.objects.filter(factory_id=complaint.factory_id, user_id=user_id, department__isnull=False).first()
            if entry and entry.department_id not in departments:
                departments.append(entry.department_id)
        if departments and complaint.department_id not in departments:
            complaint.department_id = departments[0]
            complaint.save(update_fields=["department"])


class Migration(migrations.Migration):
    dependencies = [
        ("complaints", "0010_submitted_label"),
        ("accounts", "0002_access_and_routing"),
    ]
    operations = [migrations.RunPython(follow_the_pics, migrations.RunPython.noop)]
