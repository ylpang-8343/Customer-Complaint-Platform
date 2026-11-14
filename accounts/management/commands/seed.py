"""Load the reference data every installation needs, and optionally demo users.

    python manage.py seed           # factories, complaint types, lab tests, MR permissions
    python manage.py seed --demo    # also one demo user per role and a PIC list for each factory
    python manage.py seed --demo --complaints   # and twelve demo complaints in every state (a demo site)
"""

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import COORDINATOR_GROUP, Department, Factory, FactoryPIC, Role, User
from complaints.models import (
    ComplaintMethod, ComplaintSubType, ComplaintType, Customer, LabTest, PackSize, Product, RoutingRule,
)  # fmt: skip

FACTORIES = [("FA", "Factory A"), ("FB", "Factory B"), ("FC", "Factory C"), ("FD", "Factory D"), ("FE", "Factory E")]

# Proposal section 5.2. Names ending in "Others" open a short-details field.
COMPLAINT_TYPES = {
    "Product Quality": ["Quality - Off Spec", "Quality - Foreign Matter", "Quality - Others"],
    "Packaging": [
        "Packaging - Dented",
        "Packaging - Leaking",
        "Packaging - Labeling",
        "Packaging - Damage Pallet",
        "Packaging - Others",
    ],
    "Logistic": ["Logistic - Wrong Delivery", "Logistic - Shortage", "Logistic - Transport Damage", "Logistic - Others"],
    "Documentation": [
        "Documentation - Discrepancies",
        "Documentation - Missing",
        "Documentation - Late/Delay",
        "Documentation - Others",
    ],
    "Others": [],
}

# Proposal section 5.3. The MR can change the list.
LAB_TESTS = ["FFA", "M&I", "PV", "IV", "Colour", "Appearance and odour"]

# The departments that investigate complaints, and the routing table (feedback of the demo): which department
# gets which kind of complaint. Others has no rule, so the Coordinator assigns it. The Coordinator can change both.
DEPARTMENTS = ["QA", "Production", "Operation", "Shipping"]
ROUTING = [
    ("Product Quality", "any", "QA"),
    ("Packaging", "any", "Production"),
    ("Logistic", "local", "Operation"),
    ("Logistic", "shipment", "Shipping"),
    ("Documentation", "local", "Operation"),
    ("Documentation", "shipment", "Shipping"),
]

# Open point 6: the real choices are to be given by the MR, so these are placeholders.
COMPLAINT_METHODS = ["Email", "Telephone", "Meeting", "Other"]

# Fictional master data for a demo: the customers, products and pack sizes the complaint form offers.
DEMO_CUSTOMERS = [
    ("C0001", "Example Buyer Ltd"), ("C0002", "Alpha Foods Ltd"), ("C0003", "Bravo Trading Sdn Bhd"),
    ("C0004", "Charlie Imports"), ("C0005", "Delta Foods"), ("C0006", "Echo Trading"), ("C0007", "Foxtrot Ltd"),
    ("C0008", "Golf Distribution"), ("C0009", "Hotel Foods"), ("C0010", "India Trading"), ("C0011", "Juliet Imports"),
    ("C0012", "Kilo Foods"), ("C0013", "Lima Trading"), ("C0014", "SD Malaysia Sdn Bhd"),
]  # fmt: skip
DEMO_PRODUCTS = [
    ("P0001", "Sample Product"), ("P0002", "RBD Palm Olein"), ("P0003", "RPO"), ("P0004", "PFAD"),
    ("P0005", "Refined oil"), ("P0006", "Crude oil"), ("P0007", "Packed oil"),
]  # fmt: skip
DEMO_PACK_SIZES = ["Flexibag", "Drum 200 L", "Drum", "Carton 12 x 1 L", "Jerry can 5 L", "IBC 1000 L"]

# The MR maintains the drop-down lists and each factory's PIC list and FM (open point 8).
MR_MODELS = [
    ComplaintType, ComplaintSubType, LabTest, ComplaintMethod, FactoryPIC, Factory, Customer, Product, PackSize,
    Department, RoutingRule,
]  # fmt: skip

DEMO_PASSWORD = "demo-pass-123"


class Command(BaseCommand):
    help = "Load reference data and, with --demo, demo users."

    def add_arguments(self, parser):
        parser.add_argument("--demo", action="store_true", help="Also create demo users (fictional data).")
        parser.add_argument("--complaints", action="store_true", help="With --demo: also create twelve demo complaints.")

    @transaction.atomic
    def handle(self, *args, demo=False, complaints=False, **options):
        if complaints and not demo:
            raise CommandError("--complaints needs --demo: the demo complaints are lodged by the demo users.")
        self.seed_factories()
        self.seed_complaint_types()
        self.seed_lab_tests_and_methods()
        self.seed_departments_and_routing()
        self.seed_coordinator_group()
        if demo:
            self.seed_demo_users()
            self.seed_demo_master_data()
        if complaints:
            from complaints.demo import create_demo_complaints

            made = create_demo_complaints()
            self.stdout.write(f"{made} demo complaints created." if made else "Demo complaints skipped: the database already has complaints.")
        self.stdout.write(self.style.SUCCESS("Seed complete."))

    def seed_factories(self):
        for code, name in FACTORIES:
            Factory.objects.get_or_create(code=code, defaults={"name": name})

    def seed_complaint_types(self):
        for order, (type_name, sub_names) in enumerate(COMPLAINT_TYPES.items()):
            ctype, _ = ComplaintType.objects.get_or_create(
                name=type_name,
                defaults={"sort_order": order, "requires_details": type_name == "Others"},
            )
            for sub_order, sub_name in enumerate(sub_names):
                ComplaintSubType.objects.get_or_create(
                    complaint_type=ctype,
                    name=sub_name,
                    defaults={"sort_order": sub_order, "requires_details": sub_name.endswith("Others")},
                )

    def seed_lab_tests_and_methods(self):
        for order, name in enumerate(LAB_TESTS):
            LabTest.objects.get_or_create(name=name, defaults={"sort_order": order})
        for order, name in enumerate(COMPLAINT_METHODS):
            ComplaintMethod.objects.get_or_create(name=name, defaults={"sort_order": order})

    def seed_departments_and_routing(self):
        for order, name in enumerate(DEPARTMENTS):
            Department.objects.get_or_create(name=name, defaults={"sort_order": order})
        for type_name, scope, department_name in ROUTING:
            RoutingRule.objects.get_or_create(
                complaint_type=ComplaintType.objects.get(name=type_name),
                scope=scope,
                defaults={"department": Department.objects.get(name=department_name)},
            )

    def seed_coordinator_group(self):
        group, _ = Group.objects.get_or_create(name=COORDINATOR_GROUP)
        wanted = Permission.objects.none()
        for model in MR_MODELS:
            codenames = [f"{action}_{model._meta.model_name}" for action in ("view", "change")]
            if model in (ComplaintType, ComplaintSubType, LabTest, ComplaintMethod, FactoryPIC, Customer, Product, PackSize, Department, RoutingRule):
                codenames += [f"add_{model._meta.model_name}", f"delete_{model._meta.model_name}"]
            wanted = wanted | Permission.objects.filter(
                content_type__app_label=model._meta.app_label, codename__in=codenames
            )
        group.permissions.set(wanted)
        for user in User.objects.filter(role=Role.COORDINATOR):
            user.groups.add(group)

    def seed_demo_master_data(self):
        for code, name in DEMO_CUSTOMERS:
            Customer.objects.get_or_create(code=code, defaults={"name": name})
        for code, name in DEMO_PRODUCTS:
            Product.objects.get_or_create(code=code, defaults={"name": name})
        for name in DEMO_PACK_SIZES:
            PackSize.objects.get_or_create(name=name)

    def seed_demo_users(self):
        departments = {d.name: d for d in Department.objects.all()}

        def make(username, first, last, role, factory=None, department="", dept=None, assigned=()):
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "email": f"{username}@example.com",
                    "role": role,
                    "factory": factory,
                    "department": department,
                },
            )
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save()
            wanted = departments.get(dept) if dept else None
            if wanted and user.dept_id != wanted.pk:  # also for demo users made before departments existed
                user.dept = wanted
                user.save()
            if assigned:
                user.assigned_factories.add(*assigned)
            return user

        def list_as_pic(factory, user, designation, dept):
            FactoryPIC.objects.update_or_create(
                factory=factory, user=user, defaults={"designation": designation, "department": departments.get(dept)}
            )

        make("mr", "Sample", "Coordinator", Role.COORDINATOR, department="Management Representative")
        make("management", "Sample", "Manager", Role.MANAGEMENT, department="Top management")
        make("sales", "Sample", "Sales", Role.LODGER, department="Sales")
        factories = list(Factory.objects.all())
        for factory in factories:
            code = factory.code.lower()
            fm = make(f"{code}.fm", "Sample", f"FM {factory.code}", Role.FM, factory, "Factory management")
            # Production, the department the existing demo users have always belonged to
            make(f"{code}.hod", "Sample", f"HOD {factory.code}", Role.HOD, factory, "Production", "Production")
            pic = make(f"{code}.pic", "Sample", f"PIC {factory.code}", Role.PIC, factory, "Production", "Production")
            # QA: the laboratory staff, and a QA HOD
            lab = make(f"{code}.lab", "Sample", f"Lab PIC {factory.code}", Role.LAB, factory, "Laboratory", "QA")
            lab_mgr = make(f"{code}.labmgr", "Sample", f"Lab manager {factory.code}", Role.PIC, factory, "Laboratory", "QA")
            make(f"{code}.qahod", "Sample", f"QA HOD {factory.code}", Role.HOD, factory, "QA", "QA")
            # Operation (local deliveries) and Shipping (shipments)
            op_pic = make(f"{code}.oppic", "Sample", f"Operation PIC {factory.code}", Role.PIC, factory, "Operation", "Operation")
            make(f"{code}.ophod", "Sample", f"Operation HOD {factory.code}", Role.HOD, factory, "Operation", "Operation")
            ship_pic = make(f"{code}.shippic", "Sample", f"Shipping PIC {factory.code}", Role.PIC, factory, "Shipping", "Shipping")
            make(f"{code}.shiphod", "Sample", f"Shipping HOD {factory.code}", Role.HOD, factory, "Shipping", "Shipping")
            if factory.factory_manager_id is None:
                factory.factory_manager = fm
                factory.save()
            for user, designation, dept in (
                (pic, "Production PIC", "Production"), (lab, "Lab PIC", "QA"), (lab_mgr, "Lab manager", "QA"),
                (op_pic, "Operation PIC", "Operation"), (ship_pic, "Shipping PIC", "Shipping"),
            ):  # fmt: skip
                list_as_pic(factory, user, designation, dept)

        # Users who cover several factories (access control): a trader handling the customers of Factories A and B,
        # and a group Shipping PIC who handles every factory.
        by_code = {f.code: f for f in factories}
        make("alan.trader", "Alan", "Trader", Role.TRADER, None, "Trading", assigned=[by_code[c] for c in ("FA", "FB") if c in by_code])
        group_pic = make("group.shipping", "Group", "Shipping PIC", Role.PIC, None, "Shipping", "Shipping", assigned=factories)
        for factory in factories:
            list_as_pic(factory, group_pic, "Group Shipping PIC", "Shipping")
        self.stdout.write(f"Demo users created. Password for every demo user: {DEMO_PASSWORD}")
