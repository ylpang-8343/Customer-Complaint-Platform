"""Master data: the customer, product and pack size lists, uploaded from a spreadsheet (Coordinator only)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import masterdata, people

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ERROR_LIMIT = 200
MANAGE_PAGE_SIZES = (10, 25, 50, 100)


def _coordinator_only(request):
    if people.is_coordinator(request.user):
        return None
    messages.error(request, "Only the Coordinator can maintain the customer, product and pack size lists.")
    return redirect("home")


def _dataset_or_404(slug):
    try:
        return masterdata.DATASETS[slug]
    except KeyError:
        raise Http404


def _cards(posted=None, errors=(), upload_error=""):
    cards = []
    for dataset in masterdata.DATASETS.values():
        entries = dataset.model.objects.all()
        cards.append(
            {
                "dataset": dataset,
                "total": entries.count(),
                "active": entries.filter(is_active=True).count(),
                "admin_url": f"admin:complaints_{dataset.model._meta.model_name}_changelist",
                "errors": list(errors)[:ERROR_LIMIT] if posted == dataset.slug else [],
                "more_errors": max(0, len(errors) - ERROR_LIMIT) if posted == dataset.slug else 0,
                "upload_error": upload_error if posted == dataset.slug else "",
            }
        )
    return cards


@login_required
def masterdata_home(request):
    refused = _coordinator_only(request)
    if refused:
        return refused
    return render(request, "masterdata.html", {"cards": _cards(), "max_rows": masterdata.MAX_ROWS, "max_mb": masterdata.MAX_FILE_MB})


@login_required
def masterdata_template(request, slug):
    refused = _coordinator_only(request)
    if refused:
        return refused
    dataset = _dataset_or_404(slug)
    response = HttpResponse(masterdata.build_template(dataset), content_type=XLSX_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{slug}-template.xlsx"'
    return response


@login_required
@require_POST
def masterdata_upload(request, slug):
    refused = _coordinator_only(request)
    if refused:
        return refused
    dataset = _dataset_or_404(slug)
    file = request.FILES.get("file")
    context = {"max_rows": masterdata.MAX_ROWS, "max_mb": masterdata.MAX_FILE_MB}
    if file is None:
        return render(request, "masterdata.html", {**context, "cards": _cards(slug, upload_error="Choose a file to upload.")})
    if file.size > masterdata.MAX_FILE_MB * 1024 * 1024:
        return render(request, "masterdata.html", {**context, "cards": _cards(slug, upload_error=f"The file is larger than {masterdata.MAX_FILE_MB} MB.")})
    try:
        rows, errors = masterdata.check(dataset, file, file.name)
    except masterdata.UploadError as error:
        return render(request, "masterdata.html", {**context, "cards": _cards(slug, upload_error=str(error))})
    if errors:
        return render(request, "masterdata.html", {**context, "cards": _cards(slug, errors=errors)})
    added, updated, unchanged = masterdata.apply(dataset, rows)
    messages.success(request, f"{dataset.title}: {added} added, {updated} updated, {unchanged} unchanged.")
    return redirect("masterdata_home")


def _label(entry):
    return f"{entry.code} \u2013 {entry.name}" if hasattr(entry, "code") else entry.name


@login_required
def masterdata_manage(request, slug):
    """Every entry of a list, with buttons to set it inactive or active and to delete it."""
    refused = _coordinator_only(request)
    if refused:
        return refused
    dataset = _dataset_or_404(slug)
    entries = dataset.model.objects.annotate(used=Count("complaints"))
    text = request.GET.get("q", "").strip()
    if text:
        match = Q(name__icontains=text)
        if hasattr(dataset.model, "code"):
            match |= Q(code__icontains=text)
        entries = entries.filter(match)
    state = request.GET.get("state", "")
    if state in ("active", "inactive"):
        entries = entries.filter(is_active=(state == "active"))
    entries = entries.order_by("code" if hasattr(dataset.model, "code") else "name")
    try:
        size = int(request.GET.get("page_size", MANAGE_PAGE_SIZES[0]))
    except ValueError:
        size = MANAGE_PAGE_SIZES[0]
    size = size if size in MANAGE_PAGE_SIZES else MANAGE_PAGE_SIZES[0]
    page = Paginator(entries, size).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "masterdata_manage.html",
        {
            "dataset": dataset,
            "page": page,
            "has_code": hasattr(dataset.model, "code"),
            "q": text,
            "state": state,
            "page_size": size,
            "page_sizes": MANAGE_PAGE_SIZES,
            "querystring": query.urlencode(),
            "total": dataset.model.objects.count(),
        },
    )


@login_required
@require_POST
def masterdata_entry(request, slug, pk):
    """Set an entry inactive or active, or delete it. An entry that complaints use cannot be deleted: set it inactive."""
    refused = _coordinator_only(request)
    if refused:
        return refused
    dataset = _dataset_or_404(slug)
    entry = get_object_or_404(dataset.model, pk=pk)
    action = request.POST.get("action")
    back = redirect("masterdata_manage", slug=slug)
    if action in ("activate", "deactivate"):
        entry.is_active = action == "activate"
        entry.save()
        messages.success(request, f"{_label(entry)} is now {'active' if entry.is_active else 'inactive: it is no longer offered on the form'}.")
    elif action == "delete":
        used = entry.complaints.count()
        if used:
            messages.error(
                request,
                f"{_label(entry)} is used by {used} complaint{'' if used == 1 else 's'}, so it cannot be deleted. "
                "Set it to inactive instead: it is no longer offered, and the complaints keep it.",
            )
        else:
            name = _label(entry)
            entry.delete()
            messages.success(request, f"{name} was deleted.")
    return back
