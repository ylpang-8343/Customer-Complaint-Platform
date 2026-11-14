from pathlib import Path

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.access import factories_for
from accounts.models import Factory

from . import rules
from .models import (
    ALLOWED_ATTACHMENT_EXTENSIONS,
    Attachment,
    Complaint,
    ComplaintMethod,
    ComplaintSubType,
    ComplaintType,
    Customer,
    NotificationLog,
    PackSize,
    Product,
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


PAGE_SIZES = (10, 25, 50, 100)  # complaints on a page; the first is the default


class ComplaintFilterForm(forms.Form):
    """The filter bar above the list of complaints."""

    factory = forms.ModelChoiceField(
        queryset=Factory.objects.all(), required=False, empty_label="All factories"
    )
    status = forms.ChoiceField(
        required=False, choices=[("", "All statuses"), *Complaint.Status.choices, ("deleted", "Deleted")]
    )
    complaint_type = forms.ModelChoiceField(
        queryset=ComplaintType.objects.all(), required=False, empty_label="All categories", label="Complaint category"
    )
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.all(), required=False, empty_label="All buyers", label="Buyer"
    )
    product = forms.ModelChoiceField(queryset=Product.objects.all(), required=False, empty_label="All products")
    mine = forms.BooleanField(required=False, label="Waiting for my action")
    overdue = forms.BooleanField(required=False, label="Overdue")
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    q = forms.CharField(required=False, widget=forms.TextInput(attrs={"placeholder": "Reference, buyer, product, contract..."}))
    page_size = forms.ChoiceField(
        required=False, choices=[(str(n), f"{n} per page") for n in PAGE_SIZES], initial=str(PAGE_SIZES[0]), label="Show"
    )

    def per_page(self):
        """How many complaints on a page: 10 unless the user chose another size."""
        value = self.cleaned_data.get("page_size") if hasattr(self, "cleaned_data") else ""
        return int(value) if value else PAGE_SIZES[0]

    def apply(self, queryset, waiting_for_me=None):
        data = self.cleaned_data
        if data.get("factory"):
            queryset = queryset.filter(factory=data["factory"])
        if data.get("complaint_type"):
            queryset = queryset.filter(complaint_type=data["complaint_type"])
        if data.get("customer"):
            # An imported complaint has the name only, so the name counts as well as the link.
            queryset = queryset.filter(Q(customer=data["customer"]) | Q(buyer=data["customer"].name))
        if data.get("product"):
            queryset = queryset.filter(Q(product=data["product"]) | Q(product_name=data["product"].name))
        if data.get("status") == "deleted":
            queryset = queryset.filter(deleted_at__isnull=False)
        elif data.get("status"):
            queryset = queryset.filter(status=data["status"], deleted_at__isnull=True)
        if data.get("mine") and waiting_for_me is not None:
            queryset = queryset.filter(waiting_for_me)
        if data.get("overdue"):
            queryset = queryset.overdue()
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


class ReportFilterForm(forms.Form):
    """The filter row above the dashboard, the complaint log and the Excel export.

    The factories and the lodged period decide the scope; the rest narrow the complaints further. The export
    uses the same filters, so the file holds what the screen shows.
    """

    factories = forms.ModelMultipleChoiceField(
        queryset=Factory.objects.none(), required=False, widget=forms.CheckboxSelectMultiple, label="Factories"
    )
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    status = forms.ChoiceField(
        required=False,
        choices=[("", "All statuses"), *[(v, l) for v, l in Complaint.Status.choices if v != "draft"], ("deleted", "Deleted")],
    )
    complaint_type = forms.ModelChoiceField(
        queryset=ComplaintType.objects.all(), required=False, empty_label="All categories", label="Complaint category"
    )
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.all(), required=False, empty_label="All buyers", label="Buyer"
    )
    product = forms.ModelChoiceField(queryset=Product.objects.all(), required=False, empty_label="All products")
    decision = forms.ChoiceField(
        required=False,
        label="Result",
        choices=[("", "Any result"), ("valid", "Valid"), ("invalid", "Invalid"), ("undecided", "Under investigation")],
    )
    q = forms.CharField(required=False, widget=forms.TextInput(attrs={"placeholder": "Reference, buyer, product..."}))
    page_size = forms.ChoiceField(
        required=False, choices=[(str(n), f"{n} per page") for n in PAGE_SIZES], initial=str(PAGE_SIZES[0]), label="Show"
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["factories"].queryset = factories_for(user)

    def _data(self):
        return getattr(self, "cleaned_data", {}) if self.is_bound else {}

    def scope(self):
        """(factories, date from, date to). A bad date is ignored; the form shows the error."""
        data = self._data()
        return list(data.get("factories") or []), data.get("date_from"), data.get("date_to")

    def per_page(self, default=PAGE_SIZES[0]):
        value = self._data().get("page_size")
        return int(value) if value else default

    def refine(self, queryset):
        """The scoped complaints narrowed by status, category, buyer, product, result and the search text."""
        data = self._data()
        if data.get("status") == "deleted":
            queryset = queryset.filter(deleted_at__isnull=False)
        elif data.get("status"):
            queryset = queryset.filter(status=data["status"], deleted_at__isnull=True)
        if data.get("complaint_type"):
            queryset = queryset.filter(complaint_type=data["complaint_type"])
        if data.get("customer"):
            queryset = queryset.filter(Q(customer=data["customer"]) | Q(buyer=data["customer"].name))
        if data.get("product"):
            queryset = queryset.filter(Q(product=data["product"]) | Q(product_name=data["product"].name))
        decision = data.get("decision")
        if decision == "undecided":
            queryset = queryset.filter(hod_decision="", deleted_at__isnull=True)
        elif decision:
            queryset = queryset.filter(hod_decision=decision, deleted_at__isnull=True)
        if data.get("q"):
            text = data["q"].strip()
            queryset = queryset.filter(
                Q(reference__icontains=text)
                | Q(buyer__icontains=text)
                | Q(product_name__icontains=text)
                | Q(contract_number__icontains=text)
            )
        return queryset

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("date_from"), cleaned.get("date_to")
        if start and end and end < start:
            self.add_error("date_to", "The end date is before the start date.")
        return cleaned


class NotificationFilterForm(forms.Form):
    """The filter bar of the notification log."""

    q = forms.CharField(
        required=False, widget=forms.TextInput(attrs={"placeholder": "Reference, address or subject..."})
    )
    kind = forms.ChoiceField(required=False, choices=[("", "All kinds"), *NotificationLog.Kind.choices])
    result = forms.ChoiceField(
        required=False,
        choices=[("", "All results"), ("sent", "Sent"), ("notdelivered", "Not delivered (no mail server)"), ("failed", "Failed")],
    )

    def apply(self, queryset):
        data = self.cleaned_data
        if data.get("q"):
            text = data["q"].strip()
            queryset = queryset.filter(
                Q(complaint__reference__icontains=text)
                | Q(to_addresses__icontains=text)
                | Q(cc_addresses__icontains=text)
                | Q(subject__icontains=text)
            )
        if data.get("kind"):
            queryset = queryset.filter(kind=data["kind"])
        result = data.get("result")
        if result == "sent":
            queryset = queryset.filter(success=True, delivered=True)
        elif result == "notdelivered":
            queryset = queryset.filter(success=True, delivered=False)
        elif result == "failed":
            queryset = queryset.filter(success=False)
        return queryset


DATE_FIELDS = ("received_date", "loading_date", "discharge_date", "delivery_date")


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
            "customer",
            "buyer",
            "contract_number",
            "product",
            "product_name",
            "do_reference",
            "packing_type",
            "pack_size_option",
            "pack_size",
            "affected_quantity",
            "quantity_unit",
            "transport_type",
            "vessel_name",
            "vehicle_number",
            "container_number",
            "loading_port",
            "discharge_port",
            "loading_date",
            "discharge_date",
            "delivery_date",
            "complaint_type",
            "complaint_sub_type",
            "type_details",
            "sample_sent",
            "issue_identified",
            "customer_findings",
            "customer_test_result",
            "customer_request",
            "description_others",
            "contact_person",
            "contact_email",
            "contact_phone",
            "contact_fax",
            "complaint_method",
            "received_date",
        ]
        labels = {
            "loading_port": "From (loading port / city)",
            "discharge_port": "To (discharge port / city)",
            "issue_identified": "Issue identified",
            "customer_findings": "Customer findings",
            "customer_test_result": "Customer test result",
            "customer_request": "Customer request",
            "description_others": "Others",
            "customer": "Buyer / party involved",
            "product": "Product",
            "pack_size_option": "Pack size",
        }
        widgets = {
            **{name: forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d") for name in DATE_FIELDS},
            "issue_identified": forms.Textarea(attrs={"rows": 3}),
            "customer_findings": forms.Textarea(attrs={"rows": 3}),
            "customer_test_result": forms.Textarea(attrs={"rows": 3}),
            "customer_request": forms.Textarea(attrs={"rows": 3}),
            "description_others": forms.Textarea(attrs={"rows": 3}),
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

        # Buyer, product and pack size are chosen from the lists the Coordinator keeps, so a name is never typed twice.
        # Before a list has been filled in, the name can be typed instead.
        self._list_or_text("customer", Customer, "buyer")
        self._list_or_text("product", Product, "product_name")
        self._list_or_text("pack_size_option", PackSize, "pack_size")

        # A complaint is lodged after the event, so a date cannot be in the future.
        today = timezone.localdate().isoformat()
        for name in DATE_FIELDS:
            self.fields[name].widget.attrs["max"] = today

        # The reference number carries the factory code, so the factory is fixed once it exists.
        if self.instance.reference:
            self.lock_factory()

    def _list_or_text(self, choice_name, model, text_name):
        """Keep the drop-down of a list when the list has entries, otherwise the plain text field."""
        current = getattr(self.instance, f"{choice_name}_id", None)
        options = model.objects.filter(Q(is_active=True) | Q(pk=current))
        if options.exists():
            self.fields[choice_name].queryset = options
            del self.fields[text_name]
        else:
            del self.fields[choice_name]

    def lock_factory(self):
        self.fields["factory"].disabled = True

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("factory"):
            self.add_error("factory", "Choose the factory before saving: it sets the reference number.")
        complaint_type, sub_type = cleaned.get("complaint_type"), cleaned.get("complaint_sub_type")
        if sub_type and complaint_type and sub_type.complaint_type_id != complaint_type.pk:
            self.add_error("complaint_sub_type", "This sub-type does not belong to the complaint type.")

        # The names the mandatory check looks at: the one chosen from a list, else the text already on the complaint.
        for choice_name, text_name in (("customer", "buyer"), ("product", "product_name"), ("pack_size_option", "pack_size")):
            if choice_name in self.fields:
                chosen = cleaned.get(choice_name)
                cleaned[text_name] = chosen.name if chosen else getattr(self.instance, text_name, "")

        # No date in the future, and only the dates of the chosen transport type count.
        today = timezone.localdate()
        relevant = ("received_date", *rules.TRANSPORT_FIELDS.get(cleaned.get("transport_type"), ()))
        for name in relevant:
            value = cleaned.get(name)
            if name in DATE_FIELDS and value and value > today:
                self.add_error(name, "The date cannot be in the future.")
        loading, discharge = cleaned.get("loading_date"), cleaned.get("discharge_date")
        if "loading_date" in relevant and loading and discharge and discharge < loading:
            self.add_error("discharge_date", "The discharge date is before the loading date.")
        return cleaned

    def missing_mandatory(self):
        """Mandatory fields still empty, judged on the cleaned data (used when submitting)."""
        return rules.missing_mandatory(self.cleaned_data.get)

    # A mandatory name that is chosen from a list in the form: the error is shown on the list.
    LIST_FOR_NAME = {"buyer": "customer", "product_name": "product", "pack_size": "pack_size_option"}

    def add_missing_errors(self, missing):
        for name, message in missing.items():
            target = name if name in self.fields else self.LIST_FOR_NAME.get(name)
            if target in self.fields:
                self.add_error(target, message)
            else:
                self.add_error(None, f"{name}: {message}")

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
