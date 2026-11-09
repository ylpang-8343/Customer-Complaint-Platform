"""Create twelve fictional complaints in every state, through the same workflow code the screens use."""
from datetime import date, timedelta

from django.utils import timezone

from accounts.models import Factory, FactoryPIC, User
from complaints import workflow
from complaints.models import Complaint, ComplaintMethod, ComplaintSubType, ComplaintType

mr, sales = User.objects.get(username="mr"), User.objects.get(username="sales")
email_method = ComplaintMethod.objects.get(name="Email")
now = timezone.now()


def make(code, type_name, sub_name, buyer, product, qty, stage, age, days_at_stage=None, invalid=False):
    factory = Factory.objects.get(code=code)
    prefix = code.lower()
    pic, hod, fm = (User.objects.get(username=f"{prefix}.{role}") for role in ("pic", "hod", "fm"))
    c = Complaint(
        lodged_by=sales, lodger_department="Sales", factory=factory, buyer=buyer, contract_number="SC-%06d" % (100 + Complaint.objects.count()),
        product_name=product, delivery_date=date(2026, 10, 20), packing_type="bulk", affected_quantity=qty,
        quantity_unit="mt", transport_type="local_bulk", vehicle_number="ABC 1234", loading_port="Example Loading Port",
        discharge_port="Example Destination", description=f"{sub_name}: the customer reported the problem on delivery.",
        complaint_type=ComplaintType.objects.get(name=type_name),
        complaint_sub_type=ComplaintSubType.objects.filter(name=sub_name).first(), sample_sent=False,
        contact_person="Sample Contact", contact_email="contact@example.com", complaint_method=email_method,
    )
    c.assign_reference()
    c.save()
    c.submit(sales)
    if stage != "submitted":
        entries = FactoryPIC.objects.filter(factory=factory, user=pic)
        workflow.assign(c, mr, entries, "Please investigate.")
        if stage == "pending_scar":
            workflow.save_investigation(c, pic, "Foreign matter came from a supplier's drum liner.", "Replaced the batch.", "Audit the liner supplier.", "external")
            workflow.issue_scar(c, pic, "supplier", "Liner Supplies Co.", "quality@liner.example", f"SCAR {c.reference}", "Please investigate.")
        elif stage != "investigation":
            workflow.save_investigation(c, pic, "Root cause was found and confirmed.", "Corrected the delivery.", "Added a check before dispatch.", "internal")
            workflow.submit_to_hod(c, pic)
            if stage != "hod_review":
                workflow.hod_submit_to_fm(c, hod, "invalid" if invalid else "valid", "internal", reason="The customer's own handling caused it." if invalid else "")
                if stage != "fm_review":
                    workflow.fm_decide(c, fm, True, "Approved.")
                    if stage != "approved":
                        workflow.send_reply(c, pic, f"Your complaint {c.reference}", "Dear customer, thank you for telling us. We are sorry.")
                        if stage == "closed":
                            workflow.save_verification(c, mr, date(2026, 11, 3), "No repeat in the next two deliveries.", None, "")
                            workflow.close_case(c, mr, "Effective.")
    if stage == "deleted":
        workflow.delete(c, mr, "Duplicate of an earlier complaint.")
    at_stage = days_at_stage if days_at_stage is not None else min(age, 2)
    Complaint.objects.filter(pk=c.pk).update(
        created_at=now - timedelta(days=age), submitted_at=now - timedelta(days=age), status_changed_at=now - timedelta(days=at_stage)
    )
    return c


make("FA", "Packaging", "Packaging - Leaking", "Alpha Foods Ltd", "Refined oil", 20, "closed", 40, 30)
make("FA", "Product Quality", "Quality - Off Spec", "Bravo Trading Sdn Bhd", "Refined oil", 12, "response_sent", 25, 6)
make("FA", "Logistic", "Logistic - Shortage", "Charlie Imports", "Crude oil", 8, "approved", 18, 1)
make("FA", "Packaging", "Packaging - Damage Pallet", "Delta Foods", "Packed oil", 3, "investigation", 4, 4)
make("FB", "Documentation", "Documentation - Missing", "Echo Trading", "Refined oil", 15, "fm_review", 12, 2)
make("FB", "Packaging", "Packaging - Dented", "Foxtrot Ltd", "Packed oil", 2, "hod_review", 9, 1)
make("FC", "Logistic", "Logistic - Transport Damage", "Golf Distribution", "Crude oil", 25, "investigation", 6, 5)
make("FC", "Product Quality", "Quality - Foreign Matter", "Hotel Foods", "Refined oil", 10, "pending_scar", 22, 20)
make("FD", "Packaging", "Packaging - Labeling", "India Trading", "Packed oil", 1, "submitted", 2, 2)
make("FD", "Documentation", "Documentation - Discrepancies", "Juliet Imports", "Refined oil", 5, "fm_review", 15, 3, invalid=True)
make("FE", "Product Quality", "Quality - Off Spec", "Kilo Foods", "Crude oil", 30, "closed", 70, 60)
make("FE", "Logistic", "Logistic - Wrong Delivery", "Lima Trading", "Packed oil", 4, "deleted", 30, 30)
print("created", Complaint.objects.count(), "complaints")
