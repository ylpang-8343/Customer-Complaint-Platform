import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.access import factories_for

from . import rules
from .emails import notify_coordinator_of_submission
from .forms import ComplaintFilterForm, ComplaintForm
from .help_content import GUIDE_FOR_ROLE, GUIDES, TASKS
from .models import Attachment, Complaint, ComplaintType
from .summary import summary_sections

logger = logging.getLogger(__name__)

PAGE_SIZE = 25


@login_required
def home(request):
    visible = Complaint.objects.visible_to(request.user)
    filters = ComplaintFilterForm(request.GET)
    complaints = visible.select_related("factory", "lodged_by", "complaint_type", "complaint_sub_type")
    if filters.is_valid():
        complaints = filters.apply(complaints)
    page = Paginator(complaints.order_by("-created_at", "-id"), PAGE_SIZE).get_page(request.GET.get("page"))

    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "home.html",
        {
            "factories": factories_for(request.user),
            "page": page,
            "filters": filters,
            "filtered": any(request.GET.get(name) for name in filters.fields),
            "querystring": query.urlencode(),
            "totals": {
                "visible": visible.count(),
                "drafts": visible.filter(status=Complaint.Status.DRAFT).count(),
                "submitted": visible.filter(status=Complaint.Status.SUBMITTED).count(),
            },
        },
    )


@login_required
def help_page(request):
    return render(
        request,
        "help.html",
        {
            "guides": GUIDES,
            "my_guide": GUIDE_FOR_ROLE.get(request.user.role),
            "tasks": TASKS,
        },
    )


def _visible_complaint_or_404(request, pk):
    return get_object_or_404(Complaint.objects.visible_to(request.user), pk=pk)


@login_required
def complaint_detail(request, pk):
    complaint = _visible_complaint_or_404(request, pk)
    return render(
        request,
        "complaints/complaint_detail.html",
        {
            "complaint": complaint,
            "sections": summary_sections(complaint),
            "attachments": complaint.attachments.all(),
            "can_edit": complaint.is_draft and complaint.lodged_by_id == request.user.pk,
        },
    )


def _form_config():
    return {
        "transport": {key: list(fields) for key, fields in rules.TRANSPORT_FIELDS.items()},
        "typeDetails": {
            str(t.pk): True for t in ComplaintType.objects.filter(is_active=True, requires_details=True)
        },
    }


def _render_form(request, form):
    complaint = form.instance if form.instance.pk else None
    return render(
        request,
        "complaints/complaint_form.html",
        {
            "form": form,
            "complaint": complaint,
            "attachments": complaint.attachments.all() if complaint else [],
            "mandatory": set(rules.MANDATORY_FIELDS),
            "form_config": _form_config(),
            "lodger": complaint.lodged_by if complaint else request.user,
            "lodger_department": complaint.lodger_department if complaint else request.user.department,
        },
    )


def _notify_coordinator(request, complaint):
    try:
        sent = notify_coordinator_of_submission(complaint)
    except Exception:
        logger.exception("Could not email the Coordinator about %s", complaint.reference)
        sent = 0
    if not sent:
        messages.warning(
            request,
            "The complaint was submitted, but the Coordinator could not be emailed. "
            "Please tell the Coordinator that it is waiting.",
        )


@login_required
def complaint_form(request, pk=None):
    """Lodge a new complaint, or continue a draft (steps 1 and 2 of the process)."""
    complaint = None
    if pk is not None:
        complaint = _visible_complaint_or_404(request, pk)
        if not complaint.is_draft or complaint.lodged_by_id != request.user.pk:
            messages.info(request, "Only the lodger can change a draft, and a submitted complaint is read-only.")
            return redirect("complaint_detail", pk=complaint.pk)

    if request.method != "POST":
        return _render_form(request, ComplaintForm(instance=complaint))

    form = ComplaintForm(request.POST, request.FILES, instance=complaint)
    if not form.is_valid():
        return _render_form(request, form)

    submitting = "submit" in request.POST
    missing = form.missing_mandatory() if submitting else {}
    complaint = form.save_draft(request.user)

    if not submitting:
        messages.success(request, f"Draft {complaint.reference} saved.")
        return redirect("complaint_edit", pk=complaint.pk)

    if missing:
        # Keep what was entered as a draft, and show what is still missing.
        for name, message in missing.items():
            form.add_error(name, message)
        form.add_error(None, "The draft is saved. Fill in the fields marked * to submit it to the Coordinator.")
        form.lock_factory()
        return _render_form(request, form)

    try:
        complaint.submit()
    except ValidationError as error:  # the rules changed between the check and the save
        form.add_error(None, error)
        return _render_form(request, form)
    messages.success(request, f"Complaint {complaint.reference} was submitted to the Coordinator.")
    _notify_coordinator(request, complaint)
    return redirect("complaint_detail", pk=complaint.pk)


def _visible_attachment_or_404(request, pk):
    attachment = get_object_or_404(Attachment.objects.select_related("complaint"), pk=pk)
    if not Complaint.objects.visible_to(request.user).filter(pk=attachment.complaint_id).exists():
        raise Http404
    return attachment


@login_required
def attachment_download(request, pk):
    attachment = _visible_attachment_or_404(request, pk)
    try:
        handle = attachment.file.open("rb")
    except FileNotFoundError:
        raise Http404("The file is no longer in storage.")
    return FileResponse(handle, as_attachment=True, filename=attachment.original_name)


@login_required
@require_POST
def attachment_delete(request, pk):
    attachment = _visible_attachment_or_404(request, pk)
    complaint = attachment.complaint
    if not complaint.is_draft or complaint.lodged_by_id != request.user.pk:
        messages.error(request, "Files can be removed only from your own draft.")
        return redirect("complaint_detail", pk=complaint.pk)
    name = attachment.original_name
    attachment.file.delete(save=False)
    attachment.delete()
    messages.success(request, f"Removed {name}.")
    return redirect("complaint_edit", pk=complaint.pk)
