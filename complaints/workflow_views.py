"""Screens for steps 3 to 8 of the process (Figures 3 to 9 of the proposal)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from . import texts, workflow
from .models import Attachment, Complaint, Scar
from .pdf import scar_pdf
from .report_pdf import audit_pack, complaint_report_pdf
from .views import _visible_complaint_or_404
from .workflow import WorkflowError
from .workflow_forms import (
    AssignForm,
    DeleteForm,
    FmReviewForm,
    HodReviewForm,
    InvestigationForm,
    LabForm,
    ReplyForm,
    ScarForm,
    ScarReturnForm,
    VerificationForm,
)


def _complaint_for(request, pk, allowed):
    """The complaint, or a redirect (with a message) when the user may not take this step now."""
    complaint = _visible_complaint_or_404(request, pk)
    if not allowed(request.user, complaint):
        messages.error(request, "You cannot do that on this complaint now.")
        return complaint, redirect("complaint_detail", pk=complaint.pk)
    return complaint, None


def _show_errors(form, error):
    for message in error.messages:
        form.add_error(None, message)


def _done(request, text, notified=True):
    messages.success(request, text)
    if not notified:
        messages.warning(
            request,
            "Some of the notification emails could not be sent. Please tell the people concerned "
            "that the complaint is waiting for them.",
        )


def _page(request, template, complaint, form, **extra):
    return render(
        request,
        template,
        {
            "complaint": complaint,
            "form": form,
            **extra,
        },
    )


# --- Step 3 -------------------------------------------------------------------------------


@login_required
def complaint_assign(request, pk):
    complaint, refused = _complaint_for(request, pk, workflow.can_assign)
    if refused:
        return refused
    if request.method == "POST":
        form = AssignForm(request.POST, request.FILES, complaint=complaint)
        if form.is_valid():
            try:
                notified = workflow.assign(
                    complaint, request.user, form.cleaned_data["pics"],
                    form.cleaned_data["remarks"], form.cleaned_data["attachments"],
                )  # fmt: skip
            except WorkflowError as error:
                _show_errors(form, error)
            else:
                _done(request, f"{complaint.reference} was assigned.", notified)
                return redirect("complaint_detail", pk=complaint.pk)
    else:
        form = AssignForm(complaint=complaint)
    return _page(
        request, "complaints/step_assign.html", complaint, form,
        attachments=complaint.attachments.filter(section=Attachment.Section.COORDINATOR),
    )  # fmt: skip


@login_required
def complaint_delete(request, pk):
    complaint, refused = _complaint_for(request, pk, workflow.can_delete)
    if refused:
        return refused
    form = DeleteForm(request.POST) if request.method == "POST" else DeleteForm()
    if request.method == "POST" and form.is_valid():
        try:
            workflow.delete(complaint, request.user, form.cleaned_data["reason"])
        except WorkflowError as error:
            _show_errors(form, error)
        else:
            messages.success(request, f"{complaint.reference} was marked Deleted. It stays in the log.")
            return redirect("complaint_detail", pk=complaint.pk)
    return _page(request, "complaints/step_delete.html", complaint, form)


# --- Step 4 -------------------------------------------------------------------------------


@login_required
def complaint_investigation(request, pk):
    complaint, refused = _complaint_for(request, pk, workflow.can_investigate)
    if refused:
        return refused
    if request.method == "POST":
        form = InvestigationForm(request.POST, request.FILES, complaint=complaint)
        if form.is_valid():
            data = form.cleaned_data
            try:
                workflow.save_investigation(
                    complaint, request.user, data["root_cause"], data["correction"],
                    data["corrective_action"], data["cause"], data["attachments"],
                    investigation_summary=data["investigation_summary"], conclusion=data["conclusion"],
                )  # fmt: skip
                if "submit" in request.POST:
                    notified = workflow.submit_to_hod(complaint, request.user)
                    _done(request, f"{complaint.reference} was submitted to the HOD.", notified)
                    return redirect("complaint_detail", pk=complaint.pk)
                messages.success(request, "Your findings are saved.")
                return redirect("complaint_investigation", pk=complaint.pk)
            except WorkflowError as error:
                _show_errors(form, error)
                if "submit" in request.POST:
                    form.add_error(None, "Your changes are saved. Fix the points above to submit to the HOD.")
    else:
        form = InvestigationForm(complaint=complaint)
    return _page(
        request, "complaints/step_investigation.html", complaint, form,
        attachments=complaint.attachments.filter(section=Attachment.Section.INVESTIGATION),
        lab=getattr(complaint, "lab", None),
        scars=complaint.scars.all(),
        hod_comments=complaint.events.filter(action__in=["hod_returned", "fm_rejected", "not_effective"]),
        can_submit=complaint.status == complaint.Status.INVESTIGATION,
        can_issue_scar=workflow.can_issue_scar(request.user, complaint),
    )  # fmt: skip


@login_required
def complaint_lab(request, pk):
    complaint, refused = _complaint_for(request, pk, workflow.can_edit_lab)
    if refused:
        return refused
    if request.method == "POST":
        form = LabForm(request.POST, request.FILES, complaint=complaint)
        if form.is_valid():
            try:
                workflow.save_lab(
                    complaint, request.user, form.cleaned_data["acknowledged_on"],
                    form.cleaned_data["remarks"], form.results(), form.cleaned_data["attachments"],
                )  # fmt: skip
            except WorkflowError as error:
                _show_errors(form, error)
            else:
                messages.success(request, "The laboratory section is saved.")
                return redirect("complaint_lab", pk=complaint.pk)
    else:
        form = LabForm(complaint=complaint)
    return _page(
        request, "complaints/step_lab.html", complaint, form,
        attachments=complaint.attachments.filter(section=Attachment.Section.LAB),
    )  # fmt: skip


# --- Step 4a ------------------------------------------------------------------------------


@login_required
def complaint_scar(request, pk):
    complaint, refused = _complaint_for(request, pk, workflow.can_issue_scar)
    if refused:
        return refused

    def defaults(party_name=""):
        return {
            "party_type": Scar.PartyType.SUPPLIER,
            "party_name": party_name,
            "subject": texts.scar_subject(complaint),
            "message": texts.scar_message(complaint, party_name, request.user),
        }

    if request.method == "POST" and "regenerate" in request.POST:
        # "Generate again": rebuild the wording from the template, keeping who it is for.
        initial = {**request.POST.dict(), **defaults(request.POST.get("party_name", "").strip())}
        form = ScarForm(initial=initial)
    elif request.method == "POST":
        form = ScarForm(request.POST)
        if form.is_valid():
            try:
                workflow.issue_scar(complaint, request.user, **form.cleaned_data)
            except WorkflowError as error:
                _show_errors(form, error)
            else:
                messages.success(
                    request,
                    f"The SCAR was emailed to {form.cleaned_data['party_name']}. The status is now "
                    "Pending SCAR from Supplier.",
                )
                return redirect("complaint_detail", pk=complaint.pk)
    else:
        form = ScarForm(initial=defaults())
    return _page(request, "complaints/step_scar.html", complaint, form, previous=complaint.scars.all())


@login_required
def complaint_scar_return(request, pk):
    complaint, refused = _complaint_for(request, pk, workflow.can_return_scar)
    if refused:
        return refused
    if request.method == "POST":
        form = ScarReturnForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                workflow.return_scar(complaint, request.user, form.cleaned_data["file"])
            except WorkflowError as error:
                _show_errors(form, error)
            else:
                messages.success(
                    request,
                    "The returned SCAR is uploaded. You can now complete the findings and submit them to the HOD.",
                )
                return redirect("complaint_investigation", pk=complaint.pk)
    else:
        form = ScarReturnForm()
    return _page(request, "complaints/step_scar_return.html", complaint, form, scar=complaint.scars.last())


# --- Steps 5 and 6 ---------------------------------------------------------------------------


@login_required
def complaint_hod(request, pk):
    complaint, refused = _complaint_for(request, pk, workflow.can_review_as_hod)
    if refused:
        return refused
    if request.method == "POST":
        action = "return" if "return" in request.POST else "submit"
        form = HodReviewForm(request.POST, complaint=complaint, action=action)
        if form.is_valid():
            data = form.cleaned_data
            try:
                if action == "return":
                    notified = workflow.hod_return(complaint, request.user, data["comment"])
                    _done(request, f"{complaint.reference} was returned to the PIC.", notified)
                else:
                    notified = workflow.hod_submit_to_fm(
                        complaint, request.user, data["decision"], data["cause"], data["reason"], data["comment"]
                    )
                    _done(request, f"{complaint.reference} was submitted to the FM.", notified)
                return redirect("complaint_detail", pk=complaint.pk)
            except WorkflowError as error:
                _show_errors(form, error)
    else:
        form = HodReviewForm(complaint=complaint)
    return _page(request, "complaints/step_hod.html", complaint, form, **_findings(complaint))


@login_required
def complaint_fm(request, pk):
    complaint, refused = _complaint_for(request, pk, workflow.can_review_as_fm)
    if refused:
        return refused
    if request.method == "POST":
        action = "reject" if "reject" in request.POST else "approve"
        form = FmReviewForm(request.POST, action=action)
        if form.is_valid():
            try:
                notified = workflow.fm_decide(complaint, request.user, action == "approve", form.cleaned_data["comment"])
            except WorkflowError as error:
                _show_errors(form, error)
            else:
                verb = "approved" if action == "approve" else "rejected"
                _done(request, f"{complaint.reference} was {verb}.", notified)
                return redirect("complaint_detail", pk=complaint.pk)
    else:
        form = FmReviewForm()
    return _page(request, "complaints/step_fm.html", complaint, form, **_findings(complaint))


def _findings(complaint):
    return {
        "investigation": getattr(complaint, "investigation", None),
        "lab": getattr(complaint, "lab", None),
        "scars": complaint.scars.all(),
        "history": complaint.events.exclude(comment="").select_related("actor"),
    }


# --- Step 7 -------------------------------------------------------------------------------


@login_required
def complaint_reply(request, pk):
    complaint, refused = _complaint_for(request, pk, workflow.can_reply)
    if refused:
        return refused
    problems = workflow.reply_problems(complaint)
    if request.method == "POST":
        form = ReplyForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                notified = workflow.send_reply(
                    complaint, request.user, form.cleaned_data["subject"],
                    form.cleaned_data["body"], form.cleaned_data["attachments"],
                )  # fmt: skip
            except WorkflowError as error:
                _show_errors(form, error)
            else:
                _done(request, f"The reply was sent to {complaint.contact_email}. A copy is saved.", notified)
                return redirect("complaint_detail", pk=complaint.pk)
    else:
        form = ReplyForm(
            initial={"subject": texts.reply_subject(complaint), "body": texts.reply_message(complaint, request.user)}
        )
    return _page(request, "complaints/step_reply.html", complaint, form, problems=problems)


# --- Step 8 -------------------------------------------------------------------------------


@login_required
def complaint_verify(request, pk):
    complaint, refused = _complaint_for(request, pk, workflow.can_verify)
    if refused:
        return refused
    instance = getattr(complaint, "verification", None)
    if request.method == "POST":
        action = "close" if "close" in request.POST else "not_effective" if "not_effective" in request.POST else "save"
        form = VerificationForm(request.POST, instance=instance, action=action)
        if form.is_valid():
            data = form.cleaned_data
            try:
                workflow.save_verification(
                    complaint, request.user, data["first_verified_date"], data["first_result"],
                    data["second_verified_date"], data["second_result"],
                )  # fmt: skip
                if action == "close":
                    workflow.close_case(complaint, request.user, data["comment"])
                    messages.success(request, f"{complaint.reference} is closed.")
                    return redirect("complaint_detail", pk=complaint.pk)
                if action == "not_effective":
                    notified = workflow.not_effective(complaint, request.user, data["comment"])
                    _done(request, f"{complaint.reference} was returned to the PIC.", notified)
                    return redirect("complaint_detail", pk=complaint.pk)
                messages.success(request, "The verification is saved.")
                return redirect("complaint_verify", pk=complaint.pk)
            except WorkflowError as error:
                _show_errors(form, error)
    else:
        form = VerificationForm(instance=instance)
    return _page(request, "complaints/step_verify.html", complaint, form, **_findings(complaint), reply=getattr(complaint, "reply", None))


# --- Documents ------------------------------------------------------------------------------


@login_required
def complaint_pdf(request, pk):
    complaint = _visible_complaint_or_404(request, pk)
    response = HttpResponse(complaint_report_pdf(complaint, request.user), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{complaint.reference.replace("/", "-") or "draft"}.pdf"'
    return response


@login_required
def complaint_audit_pack(request, pk):
    """The PDF report with every document of the complaint in one zip, for FSSC and ISO audits."""
    complaint = _visible_complaint_or_404(request, pk)
    if complaint.is_draft:
        messages.info(request, "A draft has no audit pack yet. It is made once the complaint is submitted.")
        return redirect("complaint_detail", pk=complaint.pk)
    response = HttpResponse(audit_pack(complaint, request.user), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{complaint.reference.replace("/", "-")}-audit-pack.zip"'
    return response


@login_required
def scar_document(request, pk):
    scar = get_object_or_404(Scar.objects.select_related("complaint", "issued_by"), pk=pk)
    if not Complaint.objects.visible_to(request.user).filter(pk=scar.complaint_id).exists():
        raise Http404
    response = HttpResponse(scar_pdf(scar), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="SCAR-{scar.complaint.reference.replace("/", "-")}.pdf"'
    return response
