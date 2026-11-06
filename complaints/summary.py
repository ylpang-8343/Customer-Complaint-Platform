"""A complaint laid out as labelled sections, for the detail page and for emails."""

from django.utils import formats, timezone


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

    sections = [
        (
            "Lodged",
            [
                ("Reference", c.reference),
                ("Status", c.status_label),
                ("Factory", str(c.factory or "")),
                ("Lodged by", f"{c.lodged_by}, {c.lodger_department}".rstrip(", ")),
                ("Lodged on", _datetime(c.created_at)),
                ("Submitted on", _datetime(c.submitted_at)),
            ],
        ),
        (
            "Complaint",
            [
                ("Buyer / party involved", c.buyer),
                ("Contract number", c.contract_number),
                ("Product", c.product_name),
                ("Delivery date", _date(c.delivery_date)),
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
                ("Loading port", c.loading_port),
                ("Port of discharge / final destination", c.discharge_port),
            ],
        ),
        ("Description", [("Description of complaint", c.description)]),
        (
            "Contact at the buyer",
            [
                ("Contact person", c.contact_person),
                ("Email", c.contact_email),
                ("Telephone", c.contact_phone),
                ("Fax", c.contact_fax),
                ("Complaint method", str(c.complaint_method or "")),
            ],
        ),
    ]
    return sections
