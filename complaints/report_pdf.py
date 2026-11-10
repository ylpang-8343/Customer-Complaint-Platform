"""The Customer Complaint Report as a PDF, and the audit pack that goes with it (proposal section 5.4, "Records").

The proposal asks for the layout of the current Customer Complaint Report. That report was not supplied, so
the sections follow what the proposal says it holds: the complaint, a laboratory section, an IMS section
(the Coordinator's), the response, approvals, the reply and the closing. To match the current layout more
closely, change the order or the wording of the sections in ``build_story``; nothing else depends on them.

Text is written in the standard PDF fonts. A character outside Western European writing is shown as ``?``
instead of an unreadable box (see ``safe``).
"""

import zipfile
from io import BytesIO
from pathlib import PurePosixPath
from xml.sax.saxutils import escape

from django.utils import formats, timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import Attachment
from .pdf import scar_pdf
from .summary import summary_sections

BRAND = colors.HexColor("#7A4A10")
ACCENT = colors.HexColor("#B5600A")
BAND = colors.HexColor("#EEF2F7")
LINE = colors.HexColor("#D9D0BF")
MUTED = colors.HexColor("#6B6254")
RED = colors.HexColor("#B3261E")

PAGE_WIDTH = A4[0] - 36 * mm  # inside the margins
LABEL_WIDTH = 50 * mm

_styles = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=_styles["BodyText"], fontSize=9, leading=11.5)
LABEL = ParagraphStyle("label", parent=BODY, textColor=MUTED)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=10)
SMALL_MUTED = ParagraphStyle("small-muted", parent=SMALL, textColor=MUTED)
SECTION = ParagraphStyle("section", parent=BODY, fontName="Helvetica-Bold", textColor=colors.white, fontSize=9.5)
TITLE = ParagraphStyle("title", parent=_styles["Title"], fontSize=17, alignment=0, textColor=BRAND, spaceAfter=0)
REFERENCE = ParagraphStyle("reference", parent=_styles["Title"], fontSize=17, alignment=2, textColor=ACCENT, spaceAfter=0)
BANNER = ParagraphStyle("banner", parent=BODY, textColor=colors.white, fontName="Helvetica-Bold")
HEAD_CELL = ParagraphStyle("head-cell", parent=SMALL, fontName="Helvetica-Bold", textColor=BRAND)


def safe(value):
    """Text for the PDF: anything the standard fonts cannot draw becomes '?'."""
    return str(value if value is not None else "").encode("cp1252", errors="replace").decode("cp1252")


def para(value, style=BODY):
    return Paragraph(escape(safe(value)).replace("\n", "<br/>"), style)


def when(value, with_time=True):
    if not value:
        return ""
    return formats.date_format(timezone.localtime(value), "j M Y, H:i" if with_time else "j M Y")


def day(value):
    return formats.date_format(value, "j M Y") if value else ""


# --- Building blocks ----------------------------------------------------------------------------------------


def section_bar(title):
    bar = Table([[Paragraph(escape(safe(title.upper())), SECTION)]], colWidths=[PAGE_WIDTH])
    bar.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), BRAND), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return bar


LONG_TEXT = 700  # characters: a value longer than this flows as paragraphs, because a table row cannot span pages
CELL_LIMIT = 1500  # characters kept in a table cell of the history and the approval records
LONG_LABEL = ParagraphStyle("long-label", parent=LABEL, fontName="Helvetica-Bold", spaceBefore=4)


def _table(rows):
    table = Table(rows, colWidths=[LABEL_WIDTH, PAGE_WIDTH - LABEL_WIDTH])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ]
        )
    )
    return table


def facts(rows):
    """Label and value rows, as a list of flowables. An empty value is a dash, so a blank in the record is visible.

    A long value is not put in a table cell (a cell cannot be split over two pages): its label goes above it
    and the text flows on as paragraphs.
    """
    flowables, short = [], []
    for label, text in rows:
        text = "" if text is None else str(text)
        if len(text) > LONG_TEXT:
            if short:
                flowables.append(_table(short))
                short = []
            flowables += [Paragraph(escape(safe(label)), LONG_LABEL), para(text), Spacer(1, 3)]
        else:
            short.append([para(label, LABEL), para(text) if text else para("-", LABEL)])
    if short:
        flowables.append(_table(short))
    return flowables


def _shorten(value):
    value = "" if value is None else str(value)
    return value if len(value) <= CELL_LIMIT else value[:CELL_LIMIT].rstrip() + " ... (shortened here; the full text is on the complaint)"


def grid(headers, rows, widths):
    """A table with a header row, for the laboratory results, the documents and the history."""
    data = [[Paragraph(escape(safe(h)), HEAD_CELL) for h in headers]]
    data += [[para(_shorten(cell), SMALL) for cell in row] for row in rows]
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BAND),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def banner(text, colour):
    table = Table([[Paragraph(escape(safe(text)).replace("\n", "<br/>"), BANNER)]], colWidths=[PAGE_WIDTH])
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colour), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    return table


def block(title, content):
    """A section: its bar kept with the first lines under it, so a bar is never left alone at the foot of a page."""
    items = []
    for item in content:
        items += item if isinstance(item, list) else [item]
    return [KeepTogether([section_bar(title), items[0]]), *items[1:], Spacer(1, 7)]


# --- The report -----------------------------------------------------------------------------------------------


def _text_block(text):
    return para(text) if text else para("-", LABEL)


def build_story(complaint):
    c = complaint
    investigation = getattr(c, "investigation", None)
    lab = getattr(c, "lab", None)
    reply = getattr(c, "reply", None)
    verification = getattr(c, "verification", None)
    events = list(c.events.select_related("actor"))
    story = []

    head = Table(
        [[Paragraph("Customer Complaint Report", TITLE), Paragraph(escape(safe(c.reference or "Draft")), REFERENCE)]],
        colWidths=[PAGE_WIDTH * 0.6, PAGE_WIDTH * 0.4],
    )
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += [head, para(f"{c.factory or 'No factory'} · Status: {c.status_label}", SMALL_MUTED), Spacer(1, 6)]
    if c.is_deleted:
        story += [banner(f"DELETED on {when(c.deleted_at)} by {c.deleted_by}.\nReason: {c.deleted_reason}", RED), Spacer(1, 6)]
    if c.is_imported:
        note = f"Imported from the previous log on {when(c.import_batch.imported_at) if c.import_batch_id else ''}. "
        note += f"Number in the previous log: {c.legacy_reference or '-'}. A field the old log did not have is blank."
        story += [banner(note, MUTED), Spacer(1, 6)]

    # 1-5: what the lodger entered (the same sections as the complaint page)
    for title, rows in summary_sections(c):
        if title == "Description":
            story += block(title, [_text_block(c.description)])
        else:
            story += block(title, [facts(rows)])

    # Coordinator (IMS) section
    assigned = [f"{a.user} ({a.designation or 'PIC'})" for a in c.assignments.select_related("user")]
    story += block(
        "Coordinator section (IMS)",
        [
            facts(
                [
                    ("Received by", str(c.received_by) if c.received_by_id else ""),
                    ("Acknowledgement date", when(c.acknowledged_at)),
                    ("Assigned PIC(s)", "\n".join(assigned)),
                    ("Remarks", c.coordinator_remarks),
                ]
            )
        ],
    )

    # Response section: the PIC's findings and any SCAR
    findings = [
        ("Investigation summary", investigation.investigation_summary if investigation else ""),
        ("Root cause analysis", investigation.root_cause if investigation else ""),
        ("Conclusion", investigation.conclusion if investigation else ""),
        ("Correction", investigation.correction if investigation else ""),
        ("Corrective and preventive action", investigation.corrective_action if investigation else ""),
        ("Cause", c.get_cause_display() if c.cause else ""),
        ("Respondent", f"{investigation.respondent}, {when(investigation.submitted_at)}" if investigation and investigation.respondent_id else ""),
    ]
    story += block("Investigation and response", [facts(findings)])
    scars = list(c.scars.select_related("issued_by", "returned_file"))
    if scars:
        rows = [
            [
                f"{s.party_name} ({s.get_party_type_display()})",
                s.contact_email,
                f"{when(s.issued_at, False)} by {s.issued_by}",
                f"Returned {when(s.returned_at, False)}: {s.returned_file.original_name if s.returned_file_id else ''}" if s.is_returned else "Not returned",
            ]
            for s in scars
        ]
        story += block("SCAR to an external party", [grid(["Issued to", "Email", "Issued", "Returned"], rows, [46 * mm, 44 * mm, 36 * mm, PAGE_WIDTH - 126 * mm])])

    # Laboratory section, which exists when a sample was sent
    if c.sample_sent or lab is not None:
        results = list(lab.results.select_related("test")) if lab else []
        parts = [
            facts(
                [
                    ("Tested by", str(lab.tested_by) if lab and lab.tested_by_id else ""),
                    ("Lab acknowledgement date", day(lab.acknowledged_on) if lab else ""),
                ]
            )
        ]
        if results:
            parts.append(
                grid(
                    ["Test", "From buyer's sample", "From retained sample"],
                    [[r.test.name, r.buyer_sample, r.retained_sample] for r in results],
                    [60 * mm, (PAGE_WIDTH - 60 * mm) / 2, (PAGE_WIDTH - 60 * mm) / 2],
                )
            )
        parts.append(facts([("Remarks", lab.remarks if lab else ""), ("Date reply to customer / trader", when(reply.sent_at, False) if reply else "")]))
        story += block("Laboratory section", parts)

    # Review and approval: the HOD's decision, then every decision in order
    story += block(
        "HOD decision",
        [
            facts(
                [
                    ("The complaint is", c.get_hod_decision_display() if c.hod_decision else ""),
                    ("Cause", c.get_cause_display() if c.cause else ""),
                    ("Reason it is invalid", c.hod_reason),
                ]
            )
        ],
    )
    decisions = [e for e in events if e.action in ("hod_returned", "hod_to_fm", "fm_approved", "fm_rejected", "not_effective")]
    if decisions:
        rows = [[when(e.created_at), e.get_action_display(), str(e.actor or ""), e.comment] for e in decisions]
        story += block("Review and approval records", [grid(["When", "Decision", "By", "Comment"], rows, [30 * mm, 48 * mm, 36 * mm, PAGE_WIDTH - 114 * mm])])

    # Reply to the customer
    if reply:
        story += block(
            "Reply to the customer",
            [
                facts(
                    [
                        ("Sent", when(reply.sent_at) if c.is_imported else f"{when(reply.sent_at)} by {reply.sent_by}"),
                        ("To", reply.to_email),
                        ("Subject", reply.subject),
                    ]
                ),
                para(reply.body),
            ],
        )
    else:
        story += block("Reply to the customer", [facts([("Sent", "")])])

    # Effectiveness and closing
    story += block(
        "Effectiveness and closing",
        [
            facts(
                [
                    ("First verified date", day(verification.first_verified_date) if verification else ""),
                    ("Result", verification.first_result if verification else ""),
                    ("Second verified date", day(verification.second_verified_date) if verification else ""),
                    ("Result", verification.second_result if verification else ""),
                    ("Date case closed", (when(verification.closed_at, False) if c.is_imported else f"{when(verification.closed_at, False)} by {verification.closed_by}") if verification and verification.closed_at else ""),
                    ("Closing comment", verification.closing_comment if verification else ""),
                ]
            )
        ],
    )

    # The documents kept with the complaint, and the history
    documents = list(c.attachments.select_related("uploaded_by"))
    if documents:
        rows = [[d.get_section_display(), d.original_name, f"{max(1, d.size // 1024)} KB", str(d.uploaded_by), when(d.uploaded_at, False)] for d in documents]
        story += block("Documents", [grid(["Section", "File", "Size", "Added by", "Date"], rows, [34 * mm, 62 * mm, 18 * mm, 36 * mm, PAGE_WIDTH - 150 * mm])])
    if events:
        rows = [[when(e.created_at), e.get_action_display(), str(e.actor or "System"), e.comment] for e in events]
        story += block("History", [grid(["When", "Step", "By", "Comment"], rows, [30 * mm, 52 * mm, 36 * mm, PAGE_WIDTH - 118 * mm])])
    return story


class NumberedCanvas(canvas.Canvas):
    """Draws "Page x of y" on every page, which is only known once the last page is made."""

    footer_text = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pages = []

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            self._footer(total)
            super().showPage()
        super().save()

    def _footer(self, total):
        self.setStrokeColor(LINE)
        self.line(18 * mm, 14 * mm, A4[0] - 18 * mm, 14 * mm)
        self.setFont("Helvetica", 7.5)
        self.setFillColor(MUTED)
        self.drawString(18 * mm, 10 * mm, self.footer_text)
        self.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {self._pageNumber} of {total}")


def complaint_report_pdf(complaint, generated_by=None):
    """The whole complaint in one PDF, for audits (FSSC, ISO) and for filing."""
    buffer = BytesIO()
    printed = f"Printed {formats.date_format(timezone.localtime(), 'j M Y, H:i')}" + (f" by {generated_by}" if generated_by else "")
    footer = safe(f"BEST Group Customer Complaint Platform · {complaint.reference or 'Draft'} · {printed}")
    canvas_class = type("ReportCanvas", (NumberedCanvas,), {"footer_text": footer})
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=15 * mm,
        bottomMargin=20 * mm,
        title=safe(f"Customer Complaint Report {complaint.reference}"),
        author="BEST Group Customer Complaint Platform",
    )
    doc.build(build_story(complaint), canvasmaker=canvas_class)
    return buffer.getvalue()


# --- The audit pack: the report and every document, in one zip ---------------------------------------------------


def _unique(name, used):
    """A file name that has not been used in the folder yet: report.pdf, report (2).pdf, ..."""
    path = PurePosixPath(name)
    candidate, number = path.name, 1
    while candidate in used:
        number += 1
        candidate = f"{path.stem} ({number}){path.suffix}"
    used.add(candidate)
    return candidate


def audit_pack(complaint, generated_by=None):
    """A zip with the PDF report, the SCARs as sent, the reply, and every attached file in a folder per section."""
    buffer = BytesIO()
    safe_ref = complaint.reference.replace("/", "-") or f"complaint-{complaint.pk}"
    missing = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{safe_ref}/{safe_ref}-report.pdf", complaint_report_pdf(complaint, generated_by))
        for scar in complaint.scars.select_related("issued_by"):
            archive.writestr(f"{safe_ref}/scar/SCAR-{safe_ref}-{scar.pk}.pdf", scar_pdf(scar))
        reply = getattr(complaint, "reply", None)
        if reply:
            by = "" if complaint.is_imported else f" by {reply.sent_by}"
            text = f"To: {reply.to_email}\nSent: {when(reply.sent_at)}{by}\nSubject: {reply.subject}\n\n{reply.body}\n"
            archive.writestr(f"{safe_ref}/reply/reply-to-customer.txt", text)
        used = {}
        for attachment in complaint.attachments.all():
            folder = {Attachment.Section.LODGING: "complaint-form"}.get(attachment.section, attachment.section)
            names = used.setdefault(folder, set())
            try:
                with attachment.file.open("rb") as handle:
                    archive.writestr(f"{safe_ref}/documents/{folder}/{_unique(attachment.original_name, names)}", handle.read())
            except FileNotFoundError:
                missing.append(f"{attachment.get_section_display()}: {attachment.original_name}")
        if missing:
            archive.writestr(
                f"{safe_ref}/MISSING-FILES.txt",
                "These files are recorded in the complaint but are no longer in storage:\n" + "\n".join(missing) + "\n",
            )
    return buffer.getvalue()
