"""The dashboard and the complaint log (proposal sections 5.5 and 6).

Everything is computed from the complaint records, so nothing is typed twice. Drafts are never counted:
a complaint is reported once it has been submitted. A deleted complaint stays in the figures, as Deleted.
"""

from collections import OrderedDict

from django.db.models import Count
from django.utils import formats, timezone

from . import people
from .models import Complaint, ComplaintType

STATUS = Complaint.Status

# The stages of a live complaint, in process order, for the "where are they" chart.
STAGE_ORDER = [
    STATUS.SUBMITTED,
    STATUS.INVESTIGATION,
    STATUS.PENDING_SCAR,
    STATUS.HOD_REVIEW,
    STATUS.FM_REVIEW,
    STATUS.APPROVED,
    STATUS.RESPONSE_SENT,
    STATUS.CLOSED,
]

# Open complaint aging: (label, first day, last day or None). Ordered, so the chart uses one hue, light to dark.
AGE_BANDS = [("0-3 days", 0, 3), ("4-7 days", 4, 7), ("8-14 days", 8, 14), ("15-30 days", 15, 30), ("Over 30 days", 31, None)]

# How many rows the dashboard tables show before pointing at the complaint log.
TABLE_LIMIT = 100
REASON_LIMIT = 20


def scoped(user, factories=None, date_from=None, date_to=None):
    """Submitted complaints the user may see, limited to the chosen factories and lodged dates."""
    queryset = Complaint.objects.visible_to(user).exclude(status=STATUS.DRAFT)
    if factories:
        queryset = queryset.filter(factory__in=factories)
    if date_from:
        queryset = queryset.filter(created_at__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(created_at__date__lte=date_to)
    return queryset


def _bars(pairs):
    """[(label, count)] -> rows with the width of each bar as a share of the largest, for the charts."""
    top = max([count for _, count in pairs], default=0)
    return [
        {"label": label, "count": count, "percent": round(100 * count / top) if top else 0}
        for label, count in pairs
    ]


def summary(queryset):
    """The figures under the complaint log and at the top of the dashboard."""
    live = queryset.filter(deleted_at__isnull=True)
    valid = live.filter(hod_decision=Complaint.HodDecision.VALID)
    return OrderedDict(
        [
            ("total", queryset.count()),
            ("under_investigation", live.filter(hod_decision="").count()),
            ("valid", valid.count()),
            ("invalid", live.filter(hod_decision=Complaint.HodDecision.INVALID).count()),
            ("deleted", queryset.filter(deleted_at__isnull=False).count()),
            ("valid_in_progress", valid.exclude(status=STATUS.CLOSED).count()),
            ("valid_closed", valid.filter(status=STATUS.CLOSED).count()),
        ]
    )


SUMMARY_LABELS = OrderedDict(
    [
        ("total", "Total number of complaints"),
        ("valid", "Valid"),
        ("invalid", "Invalid"),
        ("under_investigation", "Under investigation"),
        ("deleted", "Deleted"),
        ("valid_in_progress", "Valid complaints in progress"),
        ("valid_closed", "Valid complaints closed"),
    ]
)


def _counts_by_type(queryset):
    counts = {row["complaint_type"]: row["n"] for row in queryset.values("complaint_type").annotate(n=Count("id"))}
    pairs = [(t.name, counts.get(t.pk, 0)) for t in ComplaintType.objects.all() if t.is_active or counts.get(t.pk)]
    if counts.get(None):
        pairs.append(("Not set", counts[None]))
    return pairs


def by_type(queryset):
    return _counts_by_type(queryset)


def valid_by_type(queryset):
    """Cases for each type of valid complaint, as shown under the log (Figure 11)."""
    return _counts_by_type(
        queryset.filter(deleted_at__isnull=True, hod_decision=Complaint.HodDecision.VALID)
    )


def stage_counts(queryset):
    live = queryset.filter(deleted_at__isnull=True)
    counts = {row["status"]: row["n"] for row in live.values("status").annotate(n=Count("id"))}
    return [(STATUS(status).label, counts.get(status, 0)) for status in STAGE_ORDER]


def _opened(complaint):
    return complaint.submitted_at or complaint.created_at


def aging(queryset, now=None):
    """Open complaints with the days each has been open, oldest first, and the same as ordered bands."""
    now = now or timezone.now()
    rows = []
    for complaint in queryset.waiting().select_related("factory", "complaint_type", "complaint_sub_type").order_by(
        "submitted_at", "created_at", "id"
    ):
        days_open = max(0, (now - _opened(complaint)).days)
        rows.append(
            {
                "complaint": complaint,
                "days_open": days_open,
                "days_at_stage": complaint.days_waiting,
                "overdue": complaint.is_overdue,
                "role": people.responsible_role(complaint),
                "people": ", ".join(str(user) for user in people.responsible_users(complaint)),
            }
        )
    rows.sort(key=lambda row: -row["days_open"])
    bands = []
    for label, low, high in AGE_BANDS:
        bands.append((label, sum(1 for row in rows if row["days_open"] >= low and (high is None or row["days_open"] <= high))))
    return rows, bands


def reasons(queryset):
    """Invalid and deleted complaints with the reason given, newest first."""
    invalid = queryset.filter(deleted_at__isnull=True, hod_decision=Complaint.HodDecision.INVALID)
    deleted = queryset.filter(deleted_at__isnull=False)
    rows = [("Invalid", c, c.hod_reason) for c in invalid.order_by("-created_at")[:REASON_LIMIT]]
    rows += [("Deleted", c, c.deleted_reason) for c in deleted.order_by("-deleted_at")[:REASON_LIMIT]]
    return rows


def dashboard(queryset, now=None):
    """Everything the dashboard shows, for a queryset from ``scoped``."""
    open_rows, bands = aging(queryset, now)
    counts = summary(queryset)
    return {
        "summary": [(SUMMARY_LABELS[key], value, key) for key, value in counts.items()],
        "counts": counts,
        "by_type": _bars(by_type(queryset)),
        "valid_by_type": _bars(valid_by_type(queryset)),
        "valid_total": counts["valid"],
        "stages": _bars(stage_counts(queryset)),
        "aging_bands": _bars(bands),
        "open_total": len(open_rows),
        "open_rows": open_rows[:TABLE_LIMIT],
        "open_more": max(0, len(open_rows) - TABLE_LIMIT),
        "reasons": reasons(queryset),
    }


# --- The complaint log: one row per complaint, in the columns of the current log -----------------------------


def _local(value):
    return timezone.localtime(value) if value else None


def _date(value):
    return timezone.localtime(value).date() if value else None


def _related(complaint, name):
    return getattr(complaint, name, None)  # a missing one-to-one record reads as None


def _type_of_problem(c):
    text = str(c.complaint_sub_type or c.complaint_type or "")
    return f"{text} ({c.type_details})" if c.type_details else text


def _valid_type(c):
    valid = not c.is_deleted and c.hod_decision == Complaint.HodDecision.VALID
    return str(c.complaint_type or "") if valid else ""


def _attr(name, record):
    def getter(c):
        obj = _related(c, record)
        return getattr(obj, name) if obj is not None else None

    return getter


# (header, kind, width, getter). The order is the order of the current log; the CAR number is not used
# because the complaint reference replaces it (proposal section 5.5). "No." is added as the first column.
LOG_COLUMNS = [
    ("Customer complaint no.", "text", 18, lambda c: c.reference),
    ("Complaint from", "text", 28, lambda c: c.buyer),
    ("Complaint received by", "text", 24, lambda c: c.lodger_display),
    ("Issue date", "datetime", 18, lambda c: _local(c.created_at)),
    ("Product", "text", 24, lambda c: c.product_name),
    ("Delivery date", "date", 14, lambda c: c.delivery_date),
    ("Lab acknowledgement date", "date", 16, _attr("acknowledged_on", "lab")),
    ("Date reply to buyer / trader", "date", 16, lambda c: _date(_attr("sent_at", "reply")(c))),
    ("IMS acknowledgement date", "datetime", 18, lambda c: _local(c.acknowledged_at)),
    ("IMS remarks", "text", 34, lambda c: c.coordinator_remarks),
    ("Type of problem", "text", 30, _type_of_problem),
    ("Types of valid complaint", "text", 20, _valid_type),
    ("Correction", "text", 40, _attr("correction", "investigation")),
    ("Corrective action", "text", 40, _attr("corrective_action", "investigation")),
    ("First verified date", "date", 16, _attr("first_verified_date", "verification")),
    ("Second verified date", "date", 16, _attr("second_verified_date", "verification")),
    ("Date case closed", "date", 16, lambda c: _date(_attr("closed_at", "verification")(c))),
    ("Remarks", "text", 34, _attr("closing_comment", "verification")),
    ("Deleted", "text", 10, lambda c: "Yes" if c.is_deleted else ""),
    ("Reason delete", "text", 34, lambda c: c.deleted_reason),
]

LOG_RELATED = (
    "factory", "lodged_by", "complaint_type", "complaint_sub_type", "investigation", "lab", "reply", "verification",
)  # fmt: skip


def log_queryset(queryset):
    """The complaints in log order: oldest first, so the numbers in the No. column keep their meaning."""
    return queryset.select_related(*LOG_RELATED).order_by("created_at", "id")


def log_row(complaint):
    return [getter(complaint) for _, _, _, getter in LOG_COLUMNS]


def display(value, kind):
    """A log value as text for the page (the Excel file keeps real dates)."""
    if value in (None, ""):
        return ""
    if kind == "date":
        return formats.date_format(value, "j M Y")
    if kind == "datetime":
        return formats.date_format(value, "j M Y, H:i")
    return str(value)


def log_row_text(complaint):
    return [display(value, kind) for value, (_, kind, _, _) in zip(log_row(complaint), LOG_COLUMNS)]


def period_text(factories, date_from, date_to):
    names = ", ".join(f.name for f in factories) if factories else "all factories you can see"
    if date_from and date_to:
        period = f"lodged {date_from:%d %b %Y} to {date_to:%d %b %Y}"
    elif date_from:
        period = f"lodged from {date_from:%d %b %Y}"
    elif date_to:
        period = f"lodged up to {date_to:%d %b %Y}"
    else:
        period = "all dates"
    return f"{names}; {period}"
