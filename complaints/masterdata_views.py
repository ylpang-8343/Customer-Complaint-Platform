"""Master data: the customer, product and pack size lists, uploaded from a spreadsheet (Coordinator only)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from . import masterdata, people

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ERROR_LIMIT = 200


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
