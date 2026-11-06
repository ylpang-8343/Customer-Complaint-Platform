from pathlib import Path

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from accounts.models import Factory

from . import rules
from .models import (
    ALLOWED_ATTACHMENT_EXTENSIONS,
    Attachment,
    Complaint,
    ComplaintMethod,
    ComplaintSubType,
    ComplaintType,
)


def validate_attachment(file):
    extension = Path(file.name).suffix.lower()
    if extension not in ALLOWED_ATTACHMENT_EXTENSIONS:
        raise ValidationError(f"{file.name}: files of type {extension or 'without an extension'} are not accepted.")
    limit = settings.ATTACHMENT_MAX_MB * 1024 * 1024
    if file.size > limit:
        raise ValidationError(f"{file.name} is larger than {settings.ATTACHMENT_MAX_MB} MB.")


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        clean_one = super().clean
        files = data if isinstance(data, (list, tuple)) else [data]
        return [cleaned for cleaned in (clean_one(f, initial) for f in files) if cleaned]


class DataSelect(forms.Select):
    """A select whose options carry data attributes, which the form script uses to filter them."""

    option_data = None

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        key = getattr(value, "value", value)
        option["attrs"].update((self.option_data or {}).get(key, {}))
        return option


class ComplaintFilterForm(forms.Form):
    """The filter bar above the list of complaints."""

    factory = forms.ModelChoiceField(
        queryset=Factory.objects.all(), required=False, empty_label="All factories"
    )
    status = forms.ChoiceField(
        required=False, choices=[("", "All statuses"), *Complaint.Status.choices]
    )
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    q = forms.CharField(required=False, widget=forms.TextInput(attrs={"placeholder": "Reference, buyer, product, contract..."}))

    def apply(self, queryset):
        data = self.cleaned_data
        if data.get("factory"):
            queryset = queryset.filter(factory=data["factory"])
        if data.get("status"):
            queryset = queryset.filter(status=data["status"])
        if data.get("date_from"):
            queryset = queryset.filter(created_at__date__gte=data["date_from"])
        if data.get("date_to"):
            queryset = queryset.filter(created_at__date__lte=data["date_to"])
        if data.get("q"):
            text = data["q"].strip()
            queryset = queryset.filter(
                Q(reference__icontains=text)
                | Q(buyer__icontains=text)
                | Q(product_name__icontains=text)
                | Q(contract_number__icontains=text)
                | Q(contact_person__icontains=text)
            )
        return queryset


class ComplaintForm(forms.ModelForm):
    attachments = MultipleFileField(required=False, label="Supporting documents and pictures")
    # Yes / No / not chosen, because a draft may leave it open.
    sample_sent = forms.TypedChoiceField(
        label="Sample sent to factory",
        required=False,
        choices=[("", "---------"), ("True", "Yes"), ("False", "No")],
        coerce=lambda value: value == "True",
        empty_value=None,
    )

    class Meta:
        model = Complaint
        fields = [
            "factory",
            "buyer",
            "contract_number",
            "product_name",
            "delivery_date",
            "packing_type",
            "pack_size",
            "affected_quantity",
            "quantity_unit",
            "transport_type",
            "vessel_name",
            "vehicle_number",
            "container_number",
            "loading_port",
            "discharge_port",
            "description",
            "complaint_type",
            "complaint_sub_type",
            "type_details",
            "sample_sent",
            "do_reference",
            "contact_person",
            "contact_email",
            "contact_phone",
            "contact_fax",
            "complaint_method",
        ]
        widgets = {
            "delivery_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "description": forms.Textarea(attrs={"rows": 5}),
            "affected_quantity": forms.NumberInput(attrs={"step": "any", "min": "0"}),
            "complaint_type": DataSelect(),
            "complaint_sub_type": DataSelect(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False

        self.fields["factory"].queryset = Factory.objects.all()
        types = ComplaintType.objects.filter(is_active=True)
        sub_types = ComplaintSubType.objects.filter(is_active=True).select_related("complaint_type")
        self.fields["complaint_type"].queryset = types
        self.fields["complaint_sub_type"].queryset = sub_types
        self.fields["complaint_method"].queryset = ComplaintMethod.objects.filter(is_active=True)
        self.fields["complaint_type"].widget.option_data = {
            t.pk: {"data-details": "1" if t.requires_details else "0"} for t in types
        }
        self.fields["complaint_sub_type"].widget.option_data = {
            s.pk: {"data-type": s.complaint_type_id, "data-details": "1" if s.requires_details else "0"}
            for s in sub_types
        }

        # The reference number carries the factory code, so the factory is fixed once it exists.
        if self.instance.reference:
            self.lock_factory()

    def lock_factory(self):
        self.fields["factory"].disabled = True

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("factory"):
            self.add_error("factory", "Choose the factory before saving: it sets the reference number.")
        complaint_type, sub_type = cleaned.get("complaint_type"), cleaned.get("complaint_sub_type")
        if sub_type and complaint_type and sub_type.complaint_type_id != complaint_type.pk:
            self.add_error("complaint_sub_type", "This sub-type does not belong to the complaint type.")
        return cleaned

    def missing_mandatory(self):
        """Mandatory fields still empty, judged on the cleaned data (used when submitting)."""
        return rules.missing_mandatory(self.cleaned_data.get)

    @transaction.atomic
    def save_draft(self, user):
        """Save the complaint as entered, give it a reference number and store new attachments."""
        complaint = self.save(commit=False)
        if complaint.pk is None:
            complaint.lodged_by = user
            complaint.lodger_department = user.department
            complaint.lodger_factory = user.factory
        complaint.assign_reference()
        complaint.save()
        for file in self.cleaned_data["attachments"]:
            Attachment.objects.create(
                complaint=complaint,
                file=file,
                original_name=Path(file.name).name[:255],
                size=file.size,
                uploaded_by=user,
            )
        return complaint

    def clean_attachments(self):
        files = self.cleaned_data["attachments"]
        errors = []
        for file in files:
            try:
                validate_attachment(file)
            except ValidationError as error:
                errors.extend(error.messages)
        if errors:
            raise ValidationError(errors)
        return files
