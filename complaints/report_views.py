"""The dashboard, the complaint log and its Excel export (proposal sections 5.5 and 6)."""

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from . import exports, reporting
from .forms import ReportFilterForm

LOG_PAGE_SIZE = 10  # the default; the page size can be changed on the screen
DASHBOARD_LIST_SIZE = 10
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _scope(request):
    """The filter form and the complaints it selects. Everyone sees only what their role and factory allow."""
    form = ReportFilterForm(request.GET or None, user=request.user)
    if request.GET:
        form.is_valid()
    factories, date_from, date_to = form.scope()
    queryset = form.refine(reporting.scoped(request.user, factories, date_from, date_to))
    query = request.GET.copy()
    query.pop("page", None)
    query.pop("open_page", None)
    return form, queryset, (factories, date_from, date_to), query.urlencode()


@login_required
def dashboard(request):
    form, queryset, scope, querystring = _scope(request)
    size = form.per_page(DASHBOARD_LIST_SIZE)
    recent = reporting.log_queryset(queryset).order_by("-created_at", "-id")[:size]
    data = reporting.dashboard(queryset)
    open_page = Paginator(data.pop("open_rows"), size).get_page(request.GET.get("open_page"))
    other = request.GET.copy()
    other.pop("open_page", None)
    return render(
        request,
        "dashboard.html",
        {
            "form": form,
            "querystring": querystring,
            "filtered": bool(querystring),
            "scope_text": reporting.period_text(*scope),
            "recent": recent,
            "open_page": open_page,
            "open_querystring": other.urlencode(),
            **data,
        },
    )


@login_required
def complaint_log(request):
    form, queryset, scope, querystring = _scope(request)
    page = Paginator(reporting.log_queryset(queryset), form.per_page(LOG_PAGE_SIZE)).get_page(request.GET.get("page"))
    first_number = page.start_index() if page.paginator.count else 0
    rows = [
        {"number": first_number + index, "complaint": complaint, "values": reporting.log_row_text(complaint)}
        for index, complaint in enumerate(page.object_list)
    ]
    counts = reporting.summary(queryset)
    return render(
        request,
        "complaint_log.html",
        {
            "form": form,
            "page": page,
            "rows": rows,
            "columns": [(header, kind) for header, kind, _, _ in reporting.LOG_COLUMNS],
            "querystring": querystring,
            "summary": [(label, counts[key]) for key, label in reporting.SUMMARY_LABELS.items()],
            "valid_by_type": reporting.valid_by_type(queryset),
            "scope_text": reporting.period_text(*scope),
        },
    )


@login_required
def log_export(request):
    form, queryset, scope, _ = _scope(request)
    content = exports.build_workbook(queryset, request.user, *scope)
    response = HttpResponse(content, content_type=XLSX_TYPE)
    response["Content-Disposition"] = f'attachment; filename="complaint-log-{timezone.localdate():%Y-%m-%d}.xlsx"'
    return response
