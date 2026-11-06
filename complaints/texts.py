"""Default wording for the SCAR and the customer reply. The PIC can edit both before sending."""

from django.utils import formats


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


def reply_subject(complaint):
    return f"Your complaint {complaint.reference}: {complaint.product_name or 'our reply'}"


def reply_message(complaint, user=None):
    """The customer reply, built from the investigation, the FM's approval and the SCAR if any."""
    investigation = getattr(complaint, "investigation", None)
    lines = [
        f"Dear {complaint.contact_person or 'Sir/Madam'},",
        "",
        f"Thank you for telling us about your complaint on {complaint.product_name or 'our product'}"
        + (f" (contract {complaint.contract_number})" if complaint.contract_number else "")
        + f". Our reference is {complaint.reference}. We are sorry for the trouble this has caused.",
        "",
    ]
    if investigation:
        for heading, text in (
            ("What we found (root cause)", investigation.root_cause),
            ("What we have done (correction)", investigation.correction),
            ("What we will do to prevent it happening again (corrective and preventive action)",
             investigation.corrective_action),
        ):  # fmt: skip
            if text:
                lines += [heading + ":", text, ""]
    approval = complaint.events.filter(action="fm_approved").last()
    if approval:
        note = f" Comment: {approval.comment}" if approval.comment else ""
        lines += [f"This response was approved by {approval.actor} on {_date(approval.created_at)}.{note}", ""]
    lines += [
        "Please contact us if you would like to discuss this further.",
        "",
        "Yours sincerely,",
        str(user) if user else "",
        str(complaint.factory),
    ]
    return "\n".join(lines)
