"""Twelve fictional complaints in every state, for a demo site, the screenshots and manual testing.

They are made with the same code the screens use (``complaints.workflow``), so each one has the history, emails log
and dates a real complaint would have. Run it with ``manage.py seed --demo --complaints``. It needs the demo users
(``seed --demo``) and does nothing if the database already holds complaints.
"""

from datetime import date, timedelta

from django.test.utils import override_settings
from django.utils import timezone

from accounts.models import Factory, FactoryPIC, User

from . import workflow
from .models import Complaint, ComplaintMethod, ComplaintSubType, ComplaintType

# (factory, type, sub-type, buyer, product, tons, stage, days since lodged, days at this stage, invalid?)
DEMO_COMPLAINTS = [
    ("FA", "Packaging", "Packaging - Leaking", "Alpha Foods Ltd", "Refined oil", 20, "closed", 40, 30, False),
    ("FA", "Product Quality", "Quality - Off Spec", "Bravo Trading Sdn Bhd", "Refined oil", 12, "response_sent", 25, 6, False),
    ("FA", "Logistic", "Logistic - Shortage", "Charlie Imports", "Crude oil", 8, "approved", 18, 1, False),
    ("FA", "Packaging", "Packaging - Damage Pallet", "Delta Foods", "Packed oil", 3, "investigation", 4, 4, False),
    ("FB", "Documentation", "Documentation - Missing", "Echo Trading", "Refined oil", 15, "fm_review", 12, 2, False),
    ("FB", "Packaging", "Packaging - Dented", "Foxtrot Ltd", "Packed oil", 2, "hod_review", 9, 1, False),
    ("FC", "Logistic", "Logistic - Transport Damage", "Golf Distribution", "Crude oil", 25, "investigation", 6, 5, False),
    ("FC", "Product Quality", "Quality - Foreign Matter", "Hotel Foods", "Refined oil", 10, "pending_scar", 22, 20, False),
    ("FD", "Packaging", "Packaging - Labeling", "India Trading", "Packed oil", 1, "submitted", 2, 2, False),
    ("FD", "Documentation", "Documentation - Discrepancies", "Juliet Imports", "Refined oil", 5, "fm_review", 15, 3, True),
    ("FE", "Product Quality", "Quality - Off Spec", "Kilo Foods", "Crude oil", 30, "closed", 70, 60, False),
    ("FE", "Logistic", "Logistic - Wrong Delivery", "Lima Trading", "Packed oil", 4, "deleted", 30, 30, False),
]  # fmt: skip


def _make(spec, now):
    code, type_name, sub_name, buyer, product, tons, stage, age, at_stage, invalid = spec
    mr, sales = User.objects.get(username="mr"), User.objects.get(username="sales")
    factory = Factory.objects.get(code=code)
    prefix = code.lower()
    pic, hod, fm = (User.objects.get(username=f"{prefix}.{role}") for role in ("pic", "hod", "fm"))
    complaint = Complaint(
        lodged_by=sales, lodger_department="Sales", factory=factory, buyer=buyer,
        contract_number="SC-%06d" % (100 + Complaint.objects.count()), product_name=product,
        delivery_date=date.today() - timedelta(days=age + 5), packing_type="bulk", affected_quantity=tons,
        quantity_unit="mt", transport_type="local_bulk", vehicle_number="ABC 1234", loading_port="Example Loading Port",
        discharge_port="Example Destination", description=f"{sub_name}: the customer reported the problem on delivery.",
        complaint_type=ComplaintType.objects.get(name=type_name),
        complaint_sub_type=ComplaintSubType.objects.filter(name=sub_name).first(), sample_sent=False,
        contact_person="Sample Contact", contact_email="contact@example.com",
        complaint_method=ComplaintMethod.objects.filter(name="Email").first(),
    )  # fmt: skip
    complaint.assign_reference()
    complaint.save()
    complaint.submit(sales)
    if stage != "submitted":
        workflow.assign(complaint, mr, FactoryPIC.objects.filter(factory=factory, user=pic), "Please investigate.")
        if stage == "pending_scar":
            workflow.save_investigation(complaint, pic, "The foreign matter came from a supplier's drum liner.", "Replaced the batch.", "Audit the liner supplier.", "external")
            workflow.issue_scar(complaint, pic, "supplier", "Liner Supplies Co.", "quality@liner.example", f"SCAR {complaint.reference}", "Please investigate.")
        elif stage != "investigation":
            workflow.save_investigation(complaint, pic, "The root cause was found and confirmed.", "Corrected the delivery.", "Added a check before dispatch.", "internal")
            workflow.submit_to_hod(complaint, pic)
            if stage != "hod_review":
                reason = "The customer's own handling caused it." if invalid else ""
                workflow.hod_submit_to_fm(complaint, hod, "invalid" if invalid else "valid", "internal", reason=reason)
                if stage != "fm_review":
                    workflow.fm_decide(complaint, fm, True, "Approved.")
                    if stage != "approved":
                        workflow.send_reply(complaint, pic, f"Your complaint {complaint.reference}", "Dear customer, thank you for telling us. We are sorry.")
                        if stage == "closed":
                            workflow.save_verification(complaint, mr, date.today() - timedelta(days=2), "No repeat in the next two deliveries.", None, "")
                            workflow.close_case(complaint, mr, "Effective.")
    if stage == "deleted":
        workflow.delete(complaint, mr, "Duplicate of an earlier complaint.")
    Complaint.objects.filter(pk=complaint.pk).update(
        created_at=now - timedelta(days=age), submitted_at=now - timedelta(days=age), status_changed_at=now - timedelta(days=at_stage)
    )
    return complaint


def create_demo_complaints(force=False):
    """Create the twelve complaints. Returns how many were made (0 if complaints already exist, unless ``force``)."""
    if Complaint.objects.exists() and not force:
        return 0
    now = timezone.now()
    # The emails are not sent; the notification log still records them, so that page has something to show.
    with override_settings(EMAIL_BACKEND="django.core.mail.backends.dummy.EmailBackend"):
        for spec in DEMO_COMPLAINTS:
            _make(spec, now)
    return len(DEMO_COMPLAINTS)
