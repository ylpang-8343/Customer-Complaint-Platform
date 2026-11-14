"""A complaint laid out as labelled sections, for the detail page and for emails."""

from django.utils import formats, timezone

from . import rules


def _date(value):
    return formats.date_format(value, "j M Y") if value else ""


def _datetime(value):
    return formats.date_format(timezone.localtime(value), "j M Y, H:i") if value else ""


def _yes_no(value):
    return "" if value is None else ("Yes" if value else "No")


def summary_sections(complaint):
    """Return [(section title, [(label, text), ...]), ...]. Empty values are shown as a dash."""
    c = complaint
    packing = c.get_packing_type_display()
    if c.pack_size:
        packing = f"{packing}: {c.pack_size}"
    quantity = ""
    if c.affected_quantity is not None:
        quantity = f"{c.affected_quantity.normalize():f} {c.get_quantity_unit_display()}".strip()
    complaint_type = " / ".join(
        part for part in (str(c.complaint_type or ""), str(c.complaint_sub_type or "")) if part
    )
    local = rules.is_local(c.transport_type)
    # The code comes from the list the buyer or product was chosen from; an older complaint has the name only.
    buyer = str(c.customer) if c.customer_id else c.buyer
    product = str(c.product) if c.product_id else c.product_name

    sections = [
        (
            "Lodged",
            [
                ("Reference", c.reference),
                ("Status", c.status_label),
                ("Factory", str(c.factory or "")),
                ("Lodged by", f"{c.lodger_display}, {c.lodger_department}".rstrip(", ")),
                ("Lodged on", _datetime(c.created_at)),
                ("Submitted on", _datetime(c.submitted_at)),
            ],
        ),
        (
            "Complaint",
            [
                ("Buyer / party involved", buyer),
                ("Contract number", c.contract_number),
                ("Product", product),
                ("Packing", packing),
                ("Affected quantity", quantity),
                ("Complaint type", complaint_type),
                ("Short details", c.type_details),
                ("Sample sent to factory", _yes_no(c.sample_sent)),
                ("DO / document reference", c.do_reference),
            ],
        ),
        (
            "Transport",
            [
                ("Transport type", c.get_transport_type_display()),
                ("Vessel", c.vessel_name),
                ("Vehicle", c.vehicle_number),
                ("Container", c.container_number),
                ("Place of loading" if local else "From (loading port / city)", c.loading_port),
                ("Destination" if local else "To (discharge port / city)", c.discharge_port),
                *(
                    [("Delivery date", _date(c.delivery_date))]
                    if local
                    else [("Loading date", _date(c.loading_date)), ("Discharge date", _date(c.discharge_date))]
                ),
            ],
        ),
        (
            "Description",
            [
                ("Issue identified", c.issue_identified),
                ("Customer findings", c.customer_findings),
                ("Customer test result", c.customer_test_result),
                ("Customer request", c.customer_request),
                ("Others", c.description_others),
            ],
        ),
        (
            "Buyer contact",
            [
                ("Contact person", c.contact_person),
                ("Email", c.contact_email),
                ("Telephone", c.contact_phone),
                ("Fax", c.contact_fax),
                ("Complaint method", str(c.complaint_method or "")),
                ("Complaint received date", _date(c.received_date)),
            ],
        ),
    ]
    return sections
