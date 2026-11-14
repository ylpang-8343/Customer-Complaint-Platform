"""Create one complaint that went through every step (sample, laboratory, SCAR, a rejection, reply, closing),
for the screenshots of the PDF report. Run with:  python manage.py shell < tools/screenshots/report_data.py
"""
import datetime

from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import Factory, FactoryPIC, User
from complaints import workflow
from complaints.models import (
    Attachment, Complaint, ComplaintMethod, ComplaintSubType, ComplaintType, Customer, LabTest, PackSize, Product,
)

today = datetime.date.today()
ago = lambda days: today - datetime.timedelta(days=days)  # the platform accepts no date in the future

mr, sales = User.objects.get(username="mr"), User.objects.get(username="sales")
pic, lab, hod, fm = (User.objects.get(username=name) for name in ("fa.pic", "fa.lab", "fa.hod", "fa.fm"))
factory = Factory.objects.get(code="FA")

c = Complaint(
    lodged_by=sales, lodger_department="Sales", factory=factory, contract_number="SC-000123",
    customer=Customer.objects.get(code="C0001"), product=Product.objects.get(code="P0002"),
    pack_size_option=PackSize.objects.get(name="Flexibag"), received_date=ago(14),
    loading_date=ago(40), discharge_date=ago(16), packing_type="pack",
    affected_quantity=20, quantity_unit="mt", transport_type="shipment_container", vessel_name="MV Example",
    container_number="ABCD1234567", loading_port="Example Loading Port", discharge_port="Example Discharge Port",
    issue_identified="Flexibag leaked during discharge at the receiving terminal.",
    customer_findings="A loss of about two tons of product; photographs of the damaged flexibag and the container floor were sent.",
    customer_test_result="The buyer's own test of the remaining oil showed a free fatty acid of 0.5%.",
    customer_request="A credit note for the lost product and a written explanation.",
    complaint_type=ComplaintType.objects.get(name="Packaging"),
    complaint_sub_type=ComplaintSubType.objects.get(name="Packaging - Leaking"), sample_sent=True,
    do_reference="DO-5521", contact_person="Sample Contact", contact_email="contact@example.com",
    contact_phone="+00 0000 0000", complaint_method=ComplaintMethod.objects.get(name="Email"),
)
c.assign_reference()
c.save()
Attachment.objects.create(complaint=c, file=SimpleUploadedFile("leak-photo.png", b"\x89PNG demo"), original_name="leak-photo.png", size=9, uploaded_by=sales)
c.submit(sales)

entries = FactoryPIC.objects.filter(factory=factory, user__username__in=["fa.pic", "fa.lab"])
workflow.assign(c, mr, entries, "Please investigate and check the retained sample.")
workflow.save_investigation(
    c, pic,
    "The flexibag seal failed because of a defective weld at the supplier. The weld did not pass the pressure test "
    "because the sealing temperature was set too low on that production line.",
    "Replaced the affected flexibags and repacked the shipment before the next loading.",
    "Audit the supplier's welding process and add a seal pressure test before filling.", "external",
    [SimpleUploadedFile("weld-photo.png", b"\x89PNG demo")],
    investigation_summary=("We reviewed the packing and loading records, the supplier's weld test report and the retained "
                           "sample taken at loading. The retained sample met the specification."),
    conclusion="The leak was caused by a defective weld at the supplier, not by our filling or loading.",
)
workflow.save_lab(
    c, lab, ago(12), "Both samples are within specification except for FFA.",
    {LabTest.objects.get(name="FFA"): ("0.42%", "0.44%"), LabTest.objects.get(name="M&I"): ("2.1", "2.0"),
     LabTest.objects.get(name="Colour"): ("Pale", "Pale")},
    [SimpleUploadedFile("lab-report.pdf", b"%PDF-1.4 demo")],
)
workflow.issue_scar(c, pic, "supplier", "Flexibag Supplies Co.", "quality@flexibag.example", f"SCAR {c.reference}", "Please investigate.")
workflow.return_scar(c, pic, SimpleUploadedFile("returned-scar.pdf", b"%PDF-1.4 demo"))
workflow.submit_to_hod(c, pic)
workflow.hod_submit_to_fm(c, hod, "valid", "external", comment="Agreed with the findings.")
workflow.fm_decide(c, fm, False, "Corrective action needs a date for the supplier audit.")
workflow.save_investigation(
    c, pic, c.investigation.root_cause, c.investigation.correction,
    "Audit the supplier's welding process by 30 Nov 2026 and add a seal pressure test before filling.", "external",
)
workflow.submit_to_hod(c, pic)
workflow.hod_submit_to_fm(c, hod, "valid", "external")
workflow.fm_decide(c, fm, True, "Approved. Thank you for the quick turnaround.")
from complaints import texts  # the reply is the standard template, as the PIC would see it

workflow.send_reply(c, pic, texts.reply_subject(c), texts.reply_message(c, pic), [SimpleUploadedFile("test-report.pdf", b"%PDF-1.4 demo")])
workflow.save_verification(c, mr, ago(6), "No repeat of the leak in the next two shipments.", ago(2), "Supplier audit completed.")
workflow.close_case(c, mr, "Corrective action effective.")
print("created", c.reference, c.pk)
