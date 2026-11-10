"""Complaint records (proposal section 5.1) and the lists the MR maintains without IT."""

import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone

from accounts.access import scope_to_user

from . import rules

DEFAULT_REMINDER_DAYS = 3  # proposal section 4: a reminder after 3 days unless the type says otherwise


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
        from . import people

        visible = scope_to_user(self, user)
        # A deputy also sees the factory they act for while the HOD or FM is away.
        acting_for = people.hod_factories(user) | people.fm_factories(user)
        if acting_for:
            visible = self.filter(Q(pk__in=visible.values("pk")) | Q(factory_id__in=acting_for))
        return visible.filter(~Q(status=Complaint.Status.DRAFT) | Q(lodged_by=user))

    def waiting(self):
        """Complaints that are waiting for somebody to act: submitted, not yet closed, not deleted."""
        return self.exclude(status__in=[Complaint.Status.DRAFT, Complaint.Status.CLOSED]).filter(
            deleted_at__isnull=True
        )

    def overdue(self, now=None):
        """Waiting complaints whose current stage has gone on longer than the days set for their type."""
        now = now or timezone.now()
        due = Q(pk__in=[])
        for complaint_type in ComplaintType.objects.all():
            due |= Q(
                complaint_type=complaint_type,
                status_changed_at__lte=now - timedelta(days=complaint_type.reminder_days),
            )
        due |= Q(complaint_type__isnull=True, status_changed_at__lte=now - timedelta(days=DEFAULT_REMINDER_DAYS))
        return self.waiting().filter(due)


class Complaint(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        INVESTIGATION = "investigation", "PIC investigation"
        PENDING_SCAR = "pending_scar", "Pending SCAR from Supplier"
        HOD_REVIEW = "hod_review", "HOD review"
        FM_REVIEW = "fm_review", "FM approval"
        APPROVED = "approved", "Approved, reply due"
        RESPONSE_SENT = "response_sent", "Customer Response Sent"
        CLOSED = "closed", "Closed"

    class HodDecision(models.TextChoices):
        VALID = "valid", "Valid"
        INVALID = "invalid", "Invalid"

    class Cause(models.TextChoices):
        INTERNAL = "internal", "Internal"
        EXTERNAL = "external", "External (supplier, transporter, other party)"

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

    # Set while the complaint is handled (steps 3 to 8)
    status_changed_at = models.DateTimeField(default=timezone.now, editable=False)
    # Complaints loaded from the previous log (build step 6) carry where they came from
    is_imported = models.BooleanField(default=False, editable=False)
    legacy_reference = models.CharField(
        "Number in the previous log", max_length=60, blank=True, help_text="The number the complaint had before the platform."
    )
    imported_lodger_name = models.CharField(max_length=100, blank=True, help_text="Who received it, as written in the previous log.")
    import_batch = models.ForeignKey(
        "ImportBatch", null=True, blank=True, on_delete=models.SET_NULL, related_name="complaints", editable=False
    )
    last_reminder_at = models.DateTimeField(null=True, blank=True, editable=False)
    reminder_count = models.PositiveSmallIntegerField(default=0, editable=False)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True, editable=False)
    coordinator_remarks = models.TextField("Remarks", blank=True)
    cause = models.CharField(max_length=10, choices=Cause.choices, blank=True)
    hod_decision = models.CharField(max_length=10, choices=HodDecision.choices, blank=True)
    hod_reason = models.TextField("Reason it is invalid", blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    deleted_reason = models.TextField(blank=True)

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

    @property
    def lodger_display(self):
        """Who lodged it. For an imported complaint, the name in the previous log, never the person who imported it."""
        if self.is_imported:
            return self.imported_lodger_name or "Not recorded"
        return str(self.lodged_by)

    @property
    def is_deleted(self):
        return self.deleted_at is not None

    @property
    def status_key(self):
        """The status as shown to people: a deleted complaint reads Deleted whatever its stage."""
        return "deleted" if self.is_deleted else self.status

    @property
    def waiting_role(self):
        from . import people

        return people.responsible_role(self)

    @property
    def status_label(self):
        return "Deleted" if self.is_deleted else self.get_status_display()

    @property
    def reminder_days(self):
        return self.complaint_type.reminder_days if self.complaint_type_id else DEFAULT_REMINDER_DAYS

    @property
    def days_waiting(self):
        """Whole days the complaint has been at its current stage (0 for a draft, closed or deleted one)."""
        if not self.waiting_role:
            return 0
        return max(0, (timezone.now() - self.status_changed_at).days)

    @property
    def is_overdue(self):
        return bool(self.waiting_role) and self.days_waiting >= self.reminder_days

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

    def submit(self, actor=None):
        """Hand a complete draft to the Coordinator."""
        if not self.is_draft:
            raise ValidationError("Only a draft can be submitted.")
        missing = self.missing_mandatory()
        if missing:
            raise ValidationError(missing, code="incomplete")
        now = timezone.now()
        self.status = self.Status.SUBMITTED
        self.submitted_at = self.status_changed_at = now
        with transaction.atomic():
            self.save()
            ComplaintEvent.objects.create(
                complaint=self,
                action=ComplaintEvent.Action.SUBMITTED,
                actor=actor or self.lodged_by,
                from_status=self.Status.DRAFT,
                to_status=self.Status.SUBMITTED,
                created_at=now,
            )

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
        COORDINATOR = "coordinator", "Coordinator"
        INVESTIGATION = "investigation", "Investigation"
        LAB = "lab", "Laboratory"
        SCAR = "scar", "Returned SCAR"
        REPLY = "reply", "Reply to customer"

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


# --- Handling a submitted complaint (steps 3 to 8) -----------------------------------


class ComplaintEvent(models.Model):
    """One line of the complaint's history: what happened, who did it, and any comment."""

    class Action(models.TextChoices):
        SUBMITTED = "submitted", "Submitted to the Coordinator"
        ASSIGNED = "assigned", "Assigned to PIC(s)"
        SCAR_ISSUED = "scar_issued", "SCAR issued"
        SCAR_RETURNED = "scar_returned", "Returned SCAR uploaded"
        SENT_TO_HOD = "sent_to_hod", "Findings submitted to the HOD"
        HOD_RETURNED = "hod_returned", "Returned to the PIC by the HOD"
        HOD_TO_FM = "hod_to_fm", "HOD decision submitted to the FM"
        FM_APPROVED = "fm_approved", "Approved by the FM"
        FM_REJECTED = "fm_rejected", "Rejected by the FM"
        REPLY_SENT = "reply_sent", "Reply sent to the customer"
        NOT_EFFECTIVE = "not_effective", "Action not effective, returned to the PIC"
        CLOSED = "closed", "Case closed"
        DELETED = "deleted", "Deleted"
        IMPORTED = "imported", "Imported from the previous log"

    complaint = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name="events")
    action = models.CharField(max_length=20, choices=Action.choices)
    comment = models.TextField(blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+"
    )
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.complaint}: {self.get_action_display()}"


class Assignment(models.Model):
    """A PIC the Coordinator chose for a complaint (step 3)."""

    complaint = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name="assignments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="assignments")
    designation = models.CharField(max_length=100, blank=True)
    assigned_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["assigned_at", "id"]
        constraints = [models.UniqueConstraint(fields=["complaint", "user"], name="unique_assignment")]

    def __str__(self):
        return f"{self.complaint}: {self.user}"


class Investigation(models.Model):
    """The PIC's findings (step 4)."""

    complaint = models.OneToOneField(Complaint, on_delete=models.CASCADE, related_name="investigation")
    investigation_summary = models.TextField(
        blank=True, help_text="What was reviewed and what was found (records, samples, tests). Used in the reply to the customer."
    )
    root_cause = models.TextField("Root cause analysis", blank=True)
    conclusion = models.TextField(blank=True, help_text="The outcome, for example whether the product met specification.")
    correction = models.TextField(blank=True, help_text="What was done to deal with this complaint.")
    corrective_action = models.TextField(
        "Corrective and preventive action", blank=True, help_text="What will stop it happening again."
    )
    respondent = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Investigation of {self.complaint}"


class LabSection(models.Model):
    """The laboratory section, which opens when a sample was sent to the factory."""

    complaint = models.OneToOneField(Complaint, on_delete=models.CASCADE, related_name="lab")
    tested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    acknowledged_on = models.DateField("Lab acknowledgement date", null=True, blank=True)
    remarks = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Laboratory section of {self.complaint}"


class LabResult(models.Model):
    """One row of the test grid: a test, with the result from each of the two samples."""

    lab = models.ForeignKey(LabSection, on_delete=models.CASCADE, related_name="results")
    test = models.ForeignKey(LabTest, on_delete=models.PROTECT, related_name="+")
    buyer_sample = models.CharField("From buyer's sample", max_length=100, blank=True)
    retained_sample = models.CharField("From retained sample", max_length=100, blank=True)

    class Meta:
        ordering = ["test__sort_order", "test__name"]
        constraints = [models.UniqueConstraint(fields=["lab", "test"], name="unique_result_per_test")]

    def __str__(self):
        return f"{self.lab.complaint}: {self.test}"


class Scar(models.Model):
    """A Supplier Corrective Action Request sent to an external party (step 4a)."""

    class PartyType(models.TextChoices):
        SUPPLIER = "supplier", "Supplier"
        TRANSPORTER = "transporter", "Transporter"
        OTHER = "other", "Other party"

    complaint = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name="scars")
    party_type = models.CharField(max_length=12, choices=PartyType.choices, default=PartyType.SUPPLIER)
    party_name = models.CharField(max_length=200)
    contact_email = models.EmailField()
    subject = models.CharField(max_length=200)
    message = models.TextField()
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    issued_at = models.DateTimeField(default=timezone.now)
    returned_file = models.ForeignKey(
        Attachment, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    returned_at = models.DateTimeField(null=True, blank=True)
    returned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ["issued_at", "id"]

    def __str__(self):
        return f"SCAR {self.complaint.reference} to {self.party_name}"

    @property
    def is_returned(self):
        return self.returned_at is not None


class CustomerReply(models.Model):
    """The reply email as it was sent to the customer (step 7). A copy is kept."""

    complaint = models.OneToOneField(Complaint, on_delete=models.CASCADE, related_name="reply")
    to_email = models.EmailField()
    subject = models.CharField(max_length=200)
    body = models.TextField()
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    sent_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name_plural = "customer replies"

    def __str__(self):
        return f"Reply to {self.to_email} for {self.complaint}"


class Verification(models.Model):
    """The Coordinator's check that the corrective action worked, and the closing of the case (step 8)."""

    complaint = models.OneToOneField(Complaint, on_delete=models.CASCADE, related_name="verification")
    first_verified_date = models.DateField(null=True, blank=True)
    first_result = models.TextField(blank=True)
    second_verified_date = models.DateField(null=True, blank=True)
    second_result = models.TextField(blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    closing_comment = models.TextField(blank=True)

    def __str__(self):
        return f"Verification of {self.complaint}"


class NotificationLog(models.Model):
    """One email the platform tried to send: who it went to, what it said and whether it got out."""

    class Kind(models.TextChoices):
        SUBMITTED = "submitted", "Complaint submitted"
        ASSIGNED = "assigned", "Assigned to PIC"
        FINDINGS = "findings", "Findings to HOD"
        SCAR = "scar", "SCAR to external party"
        HOD_RETURNED = "hod_returned", "Returned by HOD"
        HOD_TO_FM = "hod_to_fm", "Response to FM"
        FM_DECISION = "fm_decision", "FM decision"
        CUSTOMER_REPLY = "customer_reply", "Reply to customer"
        REPLY_SENT = "reply_sent", "Reply sent notice"
        NOT_EFFECTIVE = "not_effective", "Action not effective"
        REMINDER = "reminder", "Reminder"
        TEST = "test", "Test email"

    complaint = models.ForeignKey(
        Complaint, null=True, blank=True, on_delete=models.CASCADE, related_name="notifications"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    subject = models.CharField(max_length=255)
    to_addresses = models.TextField(blank=True)
    cc_addresses = models.TextField(blank=True)
    body = models.TextField(blank=True)
    attachment_names = models.TextField(blank=True)
    success = models.BooleanField(default=True)
    # False when the site has no mail server set up: the email was only written to the server log.
    delivered = models.BooleanField(default=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.subject}"


def import_path(instance, filename):
    return f"imports/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


class ImportBatch(models.Model):
    """One spreadsheet of past complaints: kept so the import can be checked first and traced afterwards."""

    class Status(models.TextChoices):
        PREVIEW = "preview", "Checked, not imported yet"
        IMPORTED = "imported", "Imported"
        DISCARDED = "discarded", "Discarded"

    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(default=timezone.now)
    original_name = models.CharField(max_length=255)
    file = models.FileField(upload_to=import_path)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PREVIEW)
    rows_total = models.PositiveIntegerField(default=0)
    imported_count = models.PositiveIntegerField(default=0)
    imported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-uploaded_at", "-id"]
        verbose_name_plural = "import batches"

    def __str__(self):
        return f"{self.original_name} ({self.get_status_display()})"
