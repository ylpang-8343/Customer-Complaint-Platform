"""Complaint records (proposal section 5.1) and the lists the MR maintains without IT."""

import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone

from accounts.access import scope_to_user

from . import rules


class ComplaintType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    requires_details = models.BooleanField(
        default=False, help_text="The lodger must enter short details, as for Others."
    )
    reminder_days = models.PositiveSmallIntegerField(
        default=3,
        validators=[MinValueValidator(1)],
        help_text="A reminder is sent when an action has not been done within this many days.",
    )
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class ComplaintSubType(models.Model):
    complaint_type = models.ForeignKey(ComplaintType, on_delete=models.CASCADE, related_name="sub_types")
    name = models.CharField(max_length=100)
    requires_details = models.BooleanField(
        default=False, help_text="The lodger must enter short details, as for an Others sub-type."
    )
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["complaint_type__sort_order", "sort_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["complaint_type", "name"], name="unique_subtype_per_type")
        ]

    def __str__(self):
        return self.name


class LabTest(models.Model):
    """A row in the laboratory test grid (FFA, M&I, PV, IV, Colour, Appearance and odour)."""

    name = models.CharField(max_length=100, unique=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class ComplaintMethod(models.Model):
    """How a complaint was received. The choices are to be confirmed (open point 6)."""

    name = models.CharField(max_length=100, unique=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class ReferenceCounter(models.Model):
    """The last running number used for a factory in a year (FA/2026/0001, FA/2026/0002, ...)."""

    factory = models.ForeignKey("accounts.Factory", on_delete=models.CASCADE, related_name="+")
    year = models.PositiveSmallIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["factory", "year"], name="unique_counter_per_year")]

    def __str__(self):
        return f"{self.factory.code}/{self.year}: {self.last_number}"


class ComplaintQuerySet(models.QuerySet):
    def visible_to(self, user):
        """Complaints the user may open. A draft is visible only to the person who lodged it."""
        if not user.is_authenticated:
            return self.none()
        return scope_to_user(self, user).filter(~Q(status=Complaint.Status.DRAFT) | Q(lodged_by=user))


class Complaint(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"

    class PackingType(models.TextChoices):
        BULK = "bulk", "Bulk"
        PACK = rules.PACK, "Pack"

    class QuantityUnit(models.TextChoices):
        METRIC_TON = "mt", "Metric Ton"
        KG = "kg", "Kg"

    class TransportType(models.TextChoices):
        SHIPMENT_BULK = rules.SHIPMENT_BULK, "Shipment - Bulk"
        SHIPMENT_CONTAINER = rules.SHIPMENT_CONTAINER, "Shipment - Container"
        LOCAL_BULK = rules.LOCAL_BULK, "Local Delivery - Bulk"
        LOCAL_CONTAINER = rules.LOCAL_CONTAINER, "Local Delivery - Container"

    # Set by the system
    reference = models.CharField(max_length=20, blank=True, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    lodged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="lodged_complaints"
    )
    lodger_department = models.CharField(max_length=100, blank=True)
    lodger_factory = models.ForeignKey(
        "accounts.Factory", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    submitted_at = models.DateTimeField(null=True, blank=True, editable=False)

    # The complaint form. Everything is optional in the database so a draft can be saved
    # with fields missing; rules.missing_mandatory decides what a submission needs.
    factory = models.ForeignKey(
        "accounts.Factory", null=True, on_delete=models.PROTECT, related_name="complaints"
    )
    buyer = models.CharField("Buyer / party involved", max_length=200, blank=True)
    contract_number = models.CharField(max_length=100, blank=True)
    product_name = models.CharField(max_length=200, blank=True)
    delivery_date = models.DateField(null=True, blank=True)
    packing_type = models.CharField(max_length=10, choices=PackingType.choices, blank=True)
    pack_size = models.CharField(
        max_length=100, blank=True, help_text="For example drum, flexibag or carton."
    )
    affected_quantity = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    quantity_unit = models.CharField(max_length=5, choices=QuantityUnit.choices, blank=True)
    transport_type = models.CharField(
        "Transport type", max_length=20, choices=TransportType.choices, blank=True
    )
    vessel_name = models.CharField(max_length=100, blank=True)
    vehicle_number = models.CharField(max_length=50, blank=True)
    container_number = models.CharField(max_length=50, blank=True)
    loading_port = models.CharField(max_length=100, blank=True)
    discharge_port = models.CharField("Port of discharge / final destination", max_length=100, blank=True)
    description = models.TextField("Description of complaint", blank=True)
    complaint_type = models.ForeignKey(
        ComplaintType, null=True, blank=True, on_delete=models.PROTECT, related_name="complaints"
    )
    complaint_sub_type = models.ForeignKey(
        ComplaintSubType, null=True, blank=True, on_delete=models.PROTECT, related_name="complaints"
    )
    type_details = models.CharField("Short details", max_length=200, blank=True)
    sample_sent = models.BooleanField("Sample sent to factory", null=True, blank=True)
    do_reference = models.CharField("DO / document reference", max_length=100, blank=True)
    contact_person = models.CharField(max_length=100, blank=True)
    contact_email = models.EmailField("Email", blank=True)
    contact_phone = models.CharField("Telephone", max_length=50, blank=True)
    contact_fax = models.CharField("Fax", max_length=50, blank=True)
    complaint_method = models.ForeignKey(
        ComplaintMethod, null=True, blank=True, on_delete=models.PROTECT, related_name="complaints"
    )

    objects = ComplaintQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["reference"], condition=~Q(reference=""), name="unique_complaint_reference"
            )
        ]

    def __str__(self):
        return self.reference or f"Draft #{self.pk}"

    @property
    def is_draft(self):
        return self.status == self.Status.DRAFT

    def assign_reference(self, today=None):
        """Give the complaint the next reference of its factory, for example FA/2026/0001."""
        if self.reference:
            return
        if self.factory_id is None:
            raise ValidationError("A complaint needs a factory before it gets a reference number.")
        year = (today or timezone.localdate()).year
        with transaction.atomic():
            counter, _ = ReferenceCounter.objects.get_or_create(factory_id=self.factory_id, year=year)
            ReferenceCounter.objects.filter(pk=counter.pk).update(last_number=F("last_number") + 1)
            counter.refresh_from_db()
        self.reference = f"{self.factory.code}/{year}/{counter.last_number:04d}"

    def normalise(self):
        """Clear fields that no longer apply after a change of choice."""
        if self.packing_type != rules.PACK:
            self.pack_size = ""
        opened = rules.TRANSPORT_FIELDS.get(self.transport_type, ())
        for name in rules.ALL_TRANSPORT_FIELDS:
            if name not in opened:
                setattr(self, name, "")
        if self.complaint_type_id is None or (
            self.complaint_sub_type_id and self.complaint_sub_type.complaint_type_id != self.complaint_type_id
        ):
            self.complaint_sub_type = None
        if not rules.details_required(self.complaint_type, self.complaint_sub_type):
            self.type_details = ""

    def missing_mandatory(self):
        return rules.missing_mandatory(lambda name: getattr(self, name))

    def submit(self):
        """Hand a complete draft to the Coordinator."""
        if not self.is_draft:
            raise ValidationError("Only a draft can be submitted.")
        missing = self.missing_mandatory()
        if missing:
            raise ValidationError(missing, code="incomplete")
        self.status = self.Status.SUBMITTED
        self.submitted_at = timezone.now()
        self.save()

    def save(self, *args, **kwargs):
        self.normalise()
        super().save(*args, **kwargs)


ALLOWED_ATTACHMENT_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".doc", ".docx", ".xls",
    ".xlsx", ".csv", ".txt", ".ppt", ".pptx", ".zip", ".msg", ".eml",
}  # fmt: skip


def attachment_path(instance, filename):
    return f"attachments/{instance.complaint_id}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


class Attachment(models.Model):
    """A file added to a complaint. The original name is kept; the stored name is random."""

    class Section(models.TextChoices):
        LODGING = "lodging", "Complaint form"

    complaint = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name="attachments")
    section = models.CharField(max_length=20, choices=Section.choices, default=Section.LODGING)
    file = models.FileField(upload_to=attachment_path)
    original_name = models.CharField(max_length=255)
    size = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at", "id"]

    def __str__(self):
        return self.original_name
