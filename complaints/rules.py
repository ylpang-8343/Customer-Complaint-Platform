"""Which complaint fields are mandatory, and which fields depend on an earlier choice.

Proposal section 5.1: fields a to k and the customer's email are mandatory before a
complaint can be submitted to the Coordinator; a draft can be saved with fields missing.

``get`` is a function that returns the current value of a field by name, so the same
rules work on a model instance and on a form's cleaned data.
"""

PACK = "pack"

SHIPMENT_BULK = "shipment_bulk"
SHIPMENT_CONTAINER = "shipment_container"
LOCAL_BULK = "local_bulk"
LOCAL_CONTAINER = "local_container"

LOCAL_TYPES = (LOCAL_BULK, LOCAL_CONTAINER)

# The transport fields that open for each transport type (field g). A shipment has a loading date and a discharge
# date; a local delivery has one delivery date.
TRANSPORT_FIELDS = {
    SHIPMENT_BULK: ("vessel_name", "loading_date", "discharge_date"),
    SHIPMENT_CONTAINER: ("vessel_name", "container_number", "loading_date", "discharge_date"),
    LOCAL_BULK: ("vehicle_number", "delivery_date"),
    LOCAL_CONTAINER: ("vehicle_number", "container_number", "delivery_date"),
}
ALL_TRANSPORT_FIELDS = (
    "vessel_name", "vehicle_number", "container_number", "loading_date", "discharge_date", "delivery_date",
)  # fmt: skip
DATE_FIELDS = ("loading_date", "discharge_date", "delivery_date")


def is_local(transport_type):
    return transport_type in LOCAL_TYPES

# Fields that are always mandatory on submission (labels are shown with an asterisk).
MANDATORY_FIELDS = (
    "factory",
    "buyer",
    "contract_number",
    "product_name",
    "packing_type",
    "affected_quantity",
    "quantity_unit",
    "transport_type",
    "loading_port",
    "discharge_port",
    "issue_identified",
    "complaint_type",
    "sample_sent",
    "contact_email",
    "received_date",
)
# Fields that become mandatory because of an earlier choice.
CONDITIONAL_MANDATORY_FIELDS = (
    "pack_size",
    *ALL_TRANSPORT_FIELDS,
    "complaint_sub_type",
    "type_details",
)

REQUIRED_MESSAGE = "This field is required before the complaint can be submitted."


def is_blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


def type_has_sub_types(complaint_type):
    return complaint_type is not None and complaint_type.sub_types.filter(is_active=True).exists()


def details_required(complaint_type, sub_type):
    return bool(
        (complaint_type is not None and complaint_type.requires_details)
        or (sub_type is not None and sub_type.requires_details)
    )


def missing_mandatory(get):
    """Return {field name: message} for every mandatory field that is not filled in."""
    missing = {}

    def need(name):
        if is_blank(get(name)):
            missing[name] = REQUIRED_MESSAGE

    for name in MANDATORY_FIELDS:
        need(name)

    if get("packing_type") == PACK:
        need("pack_size")
    for name in TRANSPORT_FIELDS.get(get("transport_type"), ()):
        need(name)

    complaint_type = get("complaint_type")
    if type_has_sub_types(complaint_type):
        need("complaint_sub_type")
    if details_required(complaint_type, get("complaint_sub_type")):
        need("type_details")
    return missing
