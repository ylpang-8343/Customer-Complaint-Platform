"""PDF files made by the platform: the complaint summary mailed to PICs and the SCAR.

Step 6 of the build replaces the summary with the layout of the current Customer Complaint Report.
"""

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .summary import summary_sections

_styles = getSampleStyleSheet()
TITLE = ParagraphStyle("title", parent=_styles["Title"], fontSize=15, alignment=0, spaceAfter=4)
HEADING = ParagraphStyle("heading", parent=_styles["Heading3"], fontSize=10, spaceBefore=10, spaceAfter=3)
CELL = ParagraphStyle("cell", parent=_styles["BodyText"], fontSize=9, leading=11)
LABEL = ParagraphStyle("label", parent=CELL, textColor=colors.HexColor("#555555"))
BODY = ParagraphStyle("body", parent=_styles["BodyText"], fontSize=10, leading=13)


def _text(value):
    return escape(str(value or "")).replace("\n", "<br/>")


def _table(rows):
    data = [[Paragraph(_text(label), LABEL), Paragraph(_text(text) or "-", CELL)] for label, text in rows]
    table = Table(data, colWidths=[55 * mm, 115 * mm])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def _build(title, subtitle, blocks):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title=title,
    )  # fmt: skip
    story = [Paragraph(_text(title), TITLE), Paragraph(_text(subtitle), CELL), Spacer(1, 4)]
    story.extend(blocks)
    doc.build(story)
    return buffer.getvalue()


def complaint_summary_pdf(complaint):
    """A one-file summary of the complaint, as sent to each assigned PIC."""
    blocks = []
    for title, rows in summary_sections(complaint):
        blocks.append(Paragraph(_text(title.upper()), HEADING))
        blocks.append(_table(rows))
    return _build(
        f"Customer complaint {complaint.reference}", "BEST Group Customer Complaint Platform", blocks
    )


def scar_pdf(scar):
    """The SCAR: the request text followed by the complaint details, under the complaint reference."""
    complaint = scar.complaint
    blocks = [
        _table(
            [
                ("SCAR / complaint reference", complaint.reference),
                ("Issued to", f"{scar.party_name} ({scar.get_party_type_display()})"),
                ("Contact email", scar.contact_email),
                ("Issued on", scar.issued_at.strftime("%d %b %Y")),
                ("Issued by", f"{scar.issued_by}, {complaint.factory}"),
            ]
        ),
        Paragraph("REQUEST", HEADING),
        Paragraph(_text(scar.message), BODY),
    ]
    for title, rows in summary_sections(complaint):
        if title in ("Complaint", "Transport", "Description"):
            # The buyer's name is not passed to the external party.
            rows = [(label, text) for label, text in rows if not label.startswith("Buyer")]
            blocks.append(Paragraph(_text(title.upper()), HEADING))
            blocks.append(_table(rows))
    return _build(f"Supplier Corrective Action Request {complaint.reference}", scar.subject, blocks)
