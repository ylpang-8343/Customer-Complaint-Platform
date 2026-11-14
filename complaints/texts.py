"""Default wording for the SCAR and the customer reply. The PIC can edit both before sending.

The reply to the customer follows the recommended template in docs/emailTemplate.md: the shipment details,
then the investigation summary, root cause assessment, conclusion and corrective and preventive actions, and a
signature with the approver's name, position and company.
"""

import re

from django.conf import settings
from django.utils import formats

from . import rules

EN_DASH = "–"
COMPLAINT_SUMMARY_LIMIT = 500  # characters of the description quoted in the reply


def _date(value):
    return formats.date_format(value, "j M Y") if value else ""


def scar_subject(complaint):
    return f"Supplier Corrective Action Request {complaint.reference}"


def scar_message(complaint, party_name="", user=None):
    greeting = f"Dear {party_name}," if party_name else "Dear Sir/Madam,"
    product = complaint.product_name or "the product"
    sender = f"{user}\n{complaint.factory}" if user else str(complaint.factory)
    return (
        f"{greeting}\n\n"
        f"We have received a customer complaint about {product} that we believe is linked to your part "
        f"in the delivery. The details are in the attached SCAR, which carries our complaint reference "
        f"{complaint.reference}.\n\n"
        "Please investigate and send us the root cause, the correction and the corrective action, "
        f"quoting {complaint.reference}. We cannot reply to our customer until we have your findings.\n\n"
        f"Yours sincerely,\n{sender}"
    )


# --- The reply to the customer ----------------------------------------------------------------------------------


def reply_subject(complaint):
    """Investigation Findings for Complaint FA/2026/0005 - RPO FM PG / SD123456"""
    subject = f"Investigation Findings for Complaint {complaint.reference}"
    details = " / ".join(part for part in (complaint.product_name, complaint.contract_number) if part)
    return f"{subject} {EN_DASH} {details}" if details else subject


def _customer_name(complaint):
    if complaint.contact_person:
        return complaint.contact_person
    return f"{complaint.buyer} Team" if complaint.buyer else "Sir/Madam"


def _quantity(complaint):
    if complaint.affected_quantity is None:
        return ""
    return f"{complaint.affected_quantity.normalize():f} {complaint.get_quantity_unit_display()}".strip()


def _complaint_summary(complaint):
    """What the issue is, on one line, as the 'Complaint' line of the shipment details."""
    text = re.sub(r"\s+", " ", complaint.issue_identified or complaint.description or "").strip()
    if len(text) > COMPLAINT_SUMMARY_LIMIT:
        text = text[:COMPLAINT_SUMMARY_LIMIT].rstrip() + " ..."
    return text or str(complaint.complaint_sub_type or complaint.complaint_type or "")


def _shipment_lines(complaint):
    """The bullet list of the shipment. Only what the complaint records is listed."""
    c = complaint
    local = rules.is_local(c.transport_type)
    dates = (
        [("Delivery Date", _date(c.delivery_date))]
        if local
        else [("Loading Date", _date(c.loading_date)), ("Discharge Date", _date(c.discharge_date))]
    )
    items = [
        ("Product", c.product_name),
        ("Quantity", _quantity(c)),
        ("Vessel", c.vessel_name),
        ("Vehicle", c.vehicle_number),
        ("Container", c.container_number),
        *dates,
        ("Place of Loading" if local else "Loading Port", c.loading_port),
        ("Destination" if local else "Port of Discharge", c.discharge_port),
        ("Contract No.", c.contract_number),
        ("Complaint", _complaint_summary(c)),
    ]
    return [f"   - {label}: {value}" for label, value in items if value]


def _approver(complaint, sender):
    """Who signs: the Factory Manager who approved the response, or the person sending it if there is none."""
    approval = complaint.events.filter(action="fm_approved").select_related("actor").last()
    return approval.actor if approval and approval.actor else sender


def _signature(complaint, sender):
    person = _approver(complaint, sender)
    if person is None:
        return [str(complaint.factory), settings.COMPANY_NAME]
    position = person.get_role_display()
    if complaint.factory:
        position = f"{position}, {complaint.factory}"
    return [str(person), position, settings.COMPANY_NAME]


def reply_message(complaint, user=None):
    """The reply to the customer, built from the shipment details, the findings and the approval."""
    investigation = getattr(complaint, "investigation", None)
    lines = [
        f"Dear {_customer_name(complaint)},",
        "",
        "Thank you for notifying us regarding the quality concern for the shipment detailed below:",
        "",
        *_shipment_lines(complaint),
        "",
        "Following our investigation, we would like to share the following findings:",
        "",
    ]
    if investigation:
        for heading, text in (
            ("Investigation Summary", investigation.investigation_summary),
            ("Root Cause Assessment", investigation.root_cause),
            ("Conclusion", investigation.conclusion),
            ("Corrective and Preventive Actions", investigation.corrective_action),
        ):
            if text and text.strip():
                lines += [heading, text.strip(), ""]
    lines += [
        "We appreciate your cooperation throughout this investigation. Should you require any further "
        "clarification or wish to discuss the findings in more detail, please do not hesitate to contact us.",
        "",
        "Thank you for your continued support.",
        "",
        "Yours sincerely,",
        "",
        *_signature(complaint, user),
    ]
    return "\n".join(lines)
