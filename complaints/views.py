import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.access import factories_for
from accounts.models import Role

from . import people, rules, workflow
from .progress import progress
from . import emails, reminders
from .forms import ComplaintFilterForm, ComplaintForm, NotificationFilterForm
from .help_content import GUIDE_FOR_ROLE, GUIDES, TASKS
from .models import Attachment, Complaint, ComplaintType, NotificationLog
from .summary import summary_sections

logger = logging.getLogger(__name__)

PAGE_SIZE = 10  # the default number of complaints on a page; the user can choose more
NOTIFICATION_PAGE_SIZE = 50
NOTIFICATION_VIEWERS = (Role.COORDINATOR, Role.MANAGEMENT)  # who may read the notification log


@login_required
def home(request):
    visible = Complaint.objects.visible_to(request.user)
    filters = ComplaintFilterForm(request.GET)
    complaints = visible.select_related("factory", "lodged_by", "complaint_type", "complaint_sub_type")
    waiting = workflow.waiting_for_me(request.user)
    if filters.is_valid():
        complaints = filters.apply(complaints, waiting)
    chosen = filters.cleaned_data.get("page_size") if filters.is_valid() else ""
    size = int(chosen) if chosen else PAGE_SIZE
    page = Paginator(complaints.order_by("-created_at", "-id"), size).get_page(request.GET.get("page"))
    for complaint in page.object_list:
        complaint.waiting_text = people.waiting_text(complaint)

    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "home.html",
        {
            "factories": factories_for(request.user),
            "page": page,
            "waiting_ids": set(visible.filter(waiting).values_list("pk", flat=True)),
            "filters": filters,
            "filtered": any(request.GET.get(name) for name in filters.fields if name != "page_size"),
            "querystring": query.urlencode(),
            "totals": {
                "visible": visible.count(),
                "drafts": visible.filter(status=Complaint.Status.DRAFT, lodged_by=request.user).count(),
                "waiting": visible.filter(waiting).count(),
                "overdue": visible.overdue().count(),
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


def _by_section(complaint):
    sections = {}
    for attachment in complaint.attachments.select_related("uploaded_by"):
        sections.setdefault(attachment.section, []).append(attachment)
    return sections


@login_required
def complaint_detail(request, pk):
    complaint = _visible_complaint_or_404(request, pk)
    user = request.user
    files = _by_section(complaint)
    events = list(complaint.events.select_related("actor"))
    return render(
        request,
        "complaints/complaint_detail.html",
        {
            "complaint": complaint,
            "sections": summary_sections(complaint),
            "attachments": files.get("lodging", []),
            "files": files,
            "can_edit": complaint.is_draft and complaint.lodged_by_id == user.pk,
            "actions": workflow.available_actions(user, complaint),
            "waiting_role": people.responsible_role(complaint),
            "waiting_users": people.responsible_users(complaint),
            "assignments": complaint.assignments.select_related("user"),
            "investigation": getattr(complaint, "investigation", None),
            "lab": getattr(complaint, "lab", None),
            "scars": complaint.scars.select_related("issued_by", "returned_file"),
            "reply": getattr(complaint, "reply", None),
            "verification": getattr(complaint, "verification", None),
            "events": events,
            "notifications": (
                complaint.notifications.all() if user.role in NOTIFICATION_VIEWERS else None
            ),
            "progress": progress(complaint, events),
            "handled": not complaint.is_draft and complaint.status != Complaint.Status.SUBMITTED,
        },
    )


def _form_config():
    return {
        "transport": {key: list(fields) for key, fields in rules.TRANSPORT_FIELDS.items()},
        "allTransport": list(rules.ALL_TRANSPORT_FIELDS),
        "local": list(rules.LOCAL_TYPES),
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
            # The fields shown with an asterisk: the always-mandatory ones, the lists that stand for them, and the
            # dates of a shipment or a local delivery (each is shown only when it applies).
            "mandatory": set(rules.MANDATORY_FIELDS) | {"customer", "product", *rules.DATE_FIELDS},
            "form_config": _form_config(),
            "lodger": complaint.lodged_by if complaint else request.user,
            "lodger_department": complaint.lodger_department if complaint else request.user.department,
        },
    )


def _notify_coordinator(request, complaint):
    sent = emails.deliver(emails.notify_coordinator_of_submission, complaint)
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
        form.add_missing_errors(missing)
        form.add_error(None, "The draft is saved. Fill in the fields marked * to submit it to the Coordinator.")
        form.lock_factory()
        return _render_form(request, form)

    try:
        complaint.submit(request.user)
    except ValidationError as error:  # the rules changed between the check and the save
        form.add_error(None, error)
        return _render_form(request, form)
    routed = workflow.auto_assign(complaint)
    if routed:
        department, users, notified = routed
        messages.success(
            request,
            f"Complaint {complaint.reference} was submitted and assigned to the {department} department: "
            f"{', '.join(str(user) for user in users)}. The Coordinator is copied and can reassign it.",
        )
        if not notified:
            messages.warning(request, "The assignment email could not be sent to everyone. Please tell the PICs it is waiting.")
    else:
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
    if not workflow.can_remove_attachment(request.user, attachment):
        messages.error(request, "You cannot remove this file now.")
        return redirect("complaint_detail", pk=complaint.pk)
    name = attachment.original_name
    attachment.file.delete(save=False)
    attachment.delete()
    messages.success(request, f"Removed {name}.")
    if complaint.is_draft:
        return redirect("complaint_edit", pk=complaint.pk)
    back = {"coordinator": "complaint_assign", "investigation": "complaint_investigation", "lab": "complaint_lab"}
    return redirect(back.get(attachment.section, "complaint_detail"), pk=complaint.pk)


@login_required
def notification_log(request):
    """Every email the platform sent, or tried to send. For the Coordinator and top management."""
    if request.user.role not in NOTIFICATION_VIEWERS:
        messages.error(request, "Notifications are for the Coordinator and top management.")
        return redirect("home")
    filters = NotificationFilterForm(request.GET)
    logs = NotificationLog.objects.select_related("complaint")
    if filters.is_valid():
        logs = filters.apply(logs)
    page = Paginator(logs, NOTIFICATION_PAGE_SIZE).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "notifications.html",
        {
            "page": page,
            "filters": filters,
            "querystring": query.urlencode(),
            "failed": NotificationLog.objects.filter(success=False).count(),
            "delivers": emails.delivers_email(),
            "can_run_reminders": people.is_coordinator(request.user),
            "due_count": len(reminders.due_reminders()) if people.is_coordinator(request.user) else 0,
        },
    )


@login_required
@require_POST
def send_test_email(request):
    """Send one test email, so the Coordinator can see whether the site's email reaches people."""
    if not people.is_coordinator(request.user):
        messages.error(request, "Only the Coordinator can send a test email.")
        return redirect("home")
    address = request.POST.get("address", "").strip()
    try:
        validate_email(address)
    except ValidationError:
        messages.error(request, "Type the email address to send the test to.")
        return redirect("notifications")
    try:
        emails.send_test_email(address, request.user)
    except emails.DeliveryError as error:
        emails.record_failure(error)
        messages.error(request, f"The test email could not be sent: {error.entry['error']}")
    else:
        if emails.delivers_email():
            messages.success(request, f"A test email was sent to {address}. Check the inbox, and the spam folder.")
        else:
            messages.warning(
                request,
                "No mail server is set up, so nothing was delivered: the test email was only written to the server log. "
                "See the note at the top of this page.",
            )
    return redirect("notifications")


@login_required
@require_POST
def run_reminders(request):
    """Send the reminders that are due now: the same as `manage.py send_reminders`, for hosts with no scheduler."""
    if not people.is_coordinator(request.user):
        messages.error(request, "Only the Coordinator can send the reminders.")
        return redirect("home")
    results = reminders.send_due_reminders()
    sent = sum(1 for item in results if item["sent"])
    failed = len(results) - sent
    if not results:
        messages.info(request, "No reminders are due.")
    else:
        messages.success(request, f"{sent} reminder{'' if sent == 1 else 's'} sent.")
    if failed:
        messages.warning(request, f"{failed} could not be sent. See the failed emails below; they are tried again next time.")
    return redirect("notifications")
