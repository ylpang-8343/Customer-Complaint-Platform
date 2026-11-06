"""Forms for the steps after submission."""

from django import forms

from accounts.models import FactoryPIC, Role

from .forms import MultipleFileField, validate_attachment
from .models import Complaint, LabTest, Scar, Verification


def _check_files(files):
    errors = []
    for file in files:
        try:
            validate_attachment(file)
        except forms.ValidationError as error:
            errors.extend(error.messages)
    if errors:
        raise forms.ValidationError(errors)
    return files


class FilesMixin:
    def clean_attachments(self):
        return _check_files(self.cleaned_data["attachments"])


class PICChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, entry):
        return f"{entry.designation}: {entry.user} <{entry.user.email}>"


class AssignForm(FilesMixin, forms.Form):
    """Figure 3: choose one or more PICs of the factory, add remarks and files."""

    pics = PICChoiceField(queryset=FactoryPIC.objects.none(), widget=forms.CheckboxSelectMultiple, label="PICs")
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    attachments = MultipleFileField(required=False, label="Attachments")

    def __init__(self, *args, complaint, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["pics"].queryset = FactoryPIC.objects.filter(
            factory_id=complaint.factory_id, is_active=True, user__is_active=True
        ).select_related("user")
        if not self.is_bound:
            self.fields["pics"].initial = list(
                self.fields["pics"].queryset.filter(user__in=[a.user_id for a in complaint.assignments.all()])
            )
            self.fields["remarks"].initial = complaint.coordinator_remarks
        self.needs_lab = complaint.sample_sent is True

    def clean(self):
        cleaned = super().clean()
        pics = cleaned.get("pics")
        if pics and self.needs_lab and not any(p.user.role == Role.LAB for p in pics):
            self.add_error("pics", "A sample was sent, so choose at least one laboratory PIC to fill in the laboratory section.")
        return cleaned


class InvestigationForm(FilesMixin, forms.Form):
    root_cause = forms.CharField(label="Root cause analysis", required=False, widget=forms.Textarea(attrs={"rows": 5}))
    correction = forms.CharField(
        label="Correction", required=False, widget=forms.Textarea(attrs={"rows": 4}),
        help_text="What was done to deal with this complaint.",
    )  # fmt: skip
    corrective_action = forms.CharField(
        label="Corrective and preventive action", required=False, widget=forms.Textarea(attrs={"rows": 4}),
        help_text="What will stop it happening again.",
    )  # fmt: skip
    cause = forms.ChoiceField(
        label="Cause", required=False, choices=[("", "---------"), *Complaint.Cause.choices],
        help_text="If the cause is external, use Issue SCAR to ask the supplier or transporter.",
    )  # fmt: skip
    attachments = MultipleFileField(required=False, label="Attachments")

    def __init__(self, *args, complaint, **kwargs):
        super().__init__(*args, **kwargs)
        investigation = getattr(complaint, "investigation", None)
        if not self.is_bound:
            self.initial = {
                "root_cause": investigation.root_cause if investigation else "",
                "correction": investigation.correction if investigation else "",
                "corrective_action": investigation.corrective_action if investigation else "",
                "cause": complaint.cause,
            }


class LabForm(FilesMixin, forms.Form):
    """The laboratory section: acknowledgement date, a grid of results, remarks and the test report."""

    acknowledged_on = forms.DateField(
        label="Lab acknowledgement date", required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        help_text="The date the laboratory received the sample.",
    )  # fmt: skip
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    attachments = MultipleFileField(required=False, label="Lab attachment (for example the test report)")

    def __init__(self, *args, complaint, **kwargs):
        super().__init__(*args, **kwargs)
        self.tests = list(LabTest.objects.filter(is_active=True))
        lab = getattr(complaint, "lab", None)
        saved = {r.test_id: r for r in lab.results.all()} if lab else {}
        for test in self.tests:
            result = saved.get(test.pk)
            self.fields[f"buyer_{test.pk}"] = forms.CharField(
                required=False, max_length=100, label=f"{test.name}: from buyer's sample",
                initial=result.buyer_sample if result else "",
            )  # fmt: skip
            self.fields[f"retained_{test.pk}"] = forms.CharField(
                required=False, max_length=100, label=f"{test.name}: from retained sample",
                initial=result.retained_sample if result else "",
            )  # fmt: skip
        if lab and not self.is_bound:
            self.fields["acknowledged_on"].initial = lab.acknowledged_on
            self.fields["remarks"].initial = lab.remarks

    def grid(self):
        """Rows for the template: (test, buyer field, retained field)."""
        return [(t, self[f"buyer_{t.pk}"], self[f"retained_{t.pk}"]) for t in self.tests]

    def results(self):
        return {
            t: (self.cleaned_data[f"buyer_{t.pk}"].strip(), self.cleaned_data[f"retained_{t.pk}"].strip())
            for t in self.tests
        }


class ScarForm(forms.Form):
    """Figure 5: who the SCAR goes to, and the email that goes with it."""

    party_type = forms.ChoiceField(label="Type of party", choices=Scar.PartyType.choices)
    party_name = forms.CharField(label="Issued to", max_length=200)
    contact_email = forms.EmailField(label="Contact email")
    subject = forms.CharField(max_length=200)
    message = forms.CharField(widget=forms.Textarea(attrs={"rows": 10}))


class ScarReturnForm(FilesMixin, forms.Form):
    file = forms.FileField(label="Returned SCAR")

    def clean_file(self):
        return _check_files([self.cleaned_data["file"]])[0]


class HodReviewForm(forms.Form):
    """Figure 6. ``action`` is "submit" (to the FM) or "return" (to the PIC)."""

    decision = forms.ChoiceField(
        label="The complaint is", required=False, choices=Complaint.HodDecision.choices, widget=forms.RadioSelect
    )
    cause = forms.ChoiceField(label="Cause", required=False, choices=[("", "---------"), *Complaint.Cause.choices])
    reason = forms.CharField(
        label="Reason it is invalid", required=False, widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Needed when the complaint is invalid.",
    )  # fmt: skip
    comment = forms.CharField(
        label="Comment", required=False, widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Needed when returning the complaint to the PIC.",
    )  # fmt: skip

    def __init__(self, *args, complaint, action="submit", **kwargs):
        super().__init__(*args, **kwargs)
        self.action = action
        if not self.is_bound:
            self.initial = {"decision": complaint.hod_decision, "cause": complaint.cause, "reason": complaint.hod_reason}

    def clean(self):
        cleaned = super().clean()
        if self.action == "return":
            if not cleaned.get("comment", "").strip():
                self.add_error("comment", "Tell the PIC what to improve.")
        else:
            if not cleaned.get("decision"):
                self.add_error("decision", "Choose Valid or Invalid.")
            if not cleaned.get("cause"):
                self.add_error("cause", "Say whether the cause is internal or external.")
            if cleaned.get("decision") == Complaint.HodDecision.INVALID and not cleaned.get("reason", "").strip():
                self.add_error("reason", "Give the reason the complaint is invalid.")
        return cleaned


class FmReviewForm(forms.Form):
    comment = forms.CharField(
        label="Comment", required=False, widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Needed when rejecting the response.",
    )  # fmt: skip

    def __init__(self, *args, action="approve", **kwargs):
        super().__init__(*args, **kwargs)
        self.action = action

    def clean(self):
        cleaned = super().clean()
        if self.action == "reject" and not cleaned.get("comment", "").strip():
            self.add_error("comment", "A comment is needed to reject the response.")
        return cleaned


class ReplyForm(FilesMixin, forms.Form):
    subject = forms.CharField(max_length=200)
    body = forms.CharField(label="Message", widget=forms.Textarea(attrs={"rows": 16}))
    attachments = MultipleFileField(required=False, label="Supporting documents")


class VerificationForm(forms.ModelForm):
    """Figure 9. ``action`` is "save", "close" or "not_effective"."""

    comment = forms.CharField(
        label="Comment", required=False, widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Closing comment, or why the action was not effective (needed for that).",
    )  # fmt: skip

    class Meta:
        model = Verification
        fields = ["first_verified_date", "first_result", "second_verified_date", "second_result"]
        widgets = {
            "first_verified_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "second_verified_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "first_result": forms.Textarea(attrs={"rows": 3}),
            "second_result": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, action="save", **kwargs):
        super().__init__(*args, **kwargs)
        self.action = action
        for field in self.fields.values():
            field.required = False

    def clean(self):
        cleaned = super().clean()
        if self.action == "close":
            if not cleaned.get("first_verified_date") or not cleaned.get("first_result", "").strip():
                self.add_error("first_verified_date", "The first verified date and its result are needed to close the case.")
        if self.action == "not_effective" and not cleaned.get("comment", "").strip():
            self.add_error("comment", "Say why the action was not effective.")
        if cleaned.get("second_verified_date") and cleaned.get("first_verified_date"):
            if cleaned["second_verified_date"] < cleaned["first_verified_date"]:
                self.add_error("second_verified_date", "The second check cannot be before the first.")
        return cleaned


class DeleteForm(forms.Form):
    reason = forms.CharField(label="Reason for deleting", widget=forms.Textarea(attrs={"rows": 3}))
