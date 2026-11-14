"""Bring the complaints that already exist into the new form (feedback of the demo).

* The description becomes "Issue identified" (the form now writes the description in parts).
* A complaint received date is needed for ageing: the day the complaint was lodged is used for the old ones.
* A shipment has a loading date and a discharge date, not a delivery date: the old delivery date becomes the discharge date.
"""

from django.db import migrations

SHIPMENT = ("shipment_bulk", "shipment_container")


def forwards(apps, schema_editor):
    Complaint = apps.get_model("complaints", "Complaint")
    for complaint in Complaint.objects.all().iterator():
        changed = []
        if complaint.description and not complaint.issue_identified:
            complaint.issue_identified = complaint.description
            changed.append("issue_identified")
        if complaint.received_date is None:
            complaint.received_date = complaint.created_at.date()
            changed.append("received_date")
        if complaint.transport_type in SHIPMENT and complaint.delivery_date:
            complaint.discharge_date = complaint.delivery_date
            complaint.delivery_date = None
            changed += ["discharge_date", "delivery_date"]
        if changed:
            complaint.save(update_fields=changed)


def backwards(apps, schema_editor):
    Complaint = apps.get_model("complaints", "Complaint")
    for complaint in Complaint.objects.filter(transport_type__in=SHIPMENT, discharge_date__isnull=False):
        complaint.delivery_date = complaint.discharge_date
        complaint.save(update_fields=["delivery_date"])


class Migration(migrations.Migration):
    dependencies = [("complaints", "0007_form_feedback")]
    operations = [migrations.RunPython(forwards, backwards)]
