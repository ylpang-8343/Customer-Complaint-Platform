"""Load the reference data every installation needs, and optionally demo users.

    python manage.py seed           # factories, complaint types, lab tests, MR permissions
    python manage.py seed --demo    # also one demo user per role and a PIC list for each factory
"""

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import COORDINATOR_GROUP, Factory, FactoryPIC, Role, User
from complaints.models import ComplaintMethod, ComplaintSubType, ComplaintType, LabTest

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

# Open point 6: the real choices are to be given by the MR, so these are placeholders.
COMPLAINT_METHODS = ["Email", "Telephone", "Meeting", "Other"]

# The MR maintains the drop-down lists and each factory's PIC list and FM (open point 8).
MR_MODELS = [ComplaintType, ComplaintSubType, LabTest, ComplaintMethod, FactoryPIC, Factory]

DEMO_PASSWORD = "demo-pass-123"


class Command(BaseCommand):
    help = "Load reference data and, with --demo, demo users."

    def add_arguments(self, parser):
        parser.add_argument("--demo", action="store_true", help="Also create demo users (fictional data).")

    @transaction.atomic
    def handle(self, *args, demo=False, **options):
        self.seed_factories()
        self.seed_complaint_types()
        self.seed_lab_tests_and_methods()
        self.seed_coordinator_group()
        if demo:
            self.seed_demo_users()
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

    def seed_coordinator_group(self):
        group, _ = Group.objects.get_or_create(name=COORDINATOR_GROUP)
        wanted = Permission.objects.none()
        for model in MR_MODELS:
            codenames = [f"{action}_{model._meta.model_name}" for action in ("view", "change")]
            if model in (ComplaintType, ComplaintSubType, LabTest, ComplaintMethod, FactoryPIC):
                codenames += [f"add_{model._meta.model_name}", f"delete_{model._meta.model_name}"]
            wanted = wanted | Permission.objects.filter(
                content_type__app_label=model._meta.app_label, codename__in=codenames
            )
        group.permissions.set(wanted)
        for user in User.objects.filter(role=Role.COORDINATOR):
            user.groups.add(group)

    def seed_demo_users(self):
        def make(username, first, last, role, factory=None, department=""):
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
            return user

        make("mr", "Sample", "Coordinator", Role.COORDINATOR, department="Management Representative")
        make("management", "Sample", "Manager", Role.MANAGEMENT, department="Top management")
        make("sales", "Sample", "Sales", Role.LODGER, department="Sales")
        for factory in Factory.objects.all():
            code = factory.code.lower()
            fm = make(f"{code}.fm", "Sample", f"FM {factory.code}", Role.FM, factory, "Factory management")
            make(f"{code}.hod", "Sample", f"HOD {factory.code}", Role.HOD, factory, "Production")
            pic = make(f"{code}.pic", "Sample", f"PIC {factory.code}", Role.PIC, factory, "Production")
            lab = make(f"{code}.lab", "Sample", f"Lab PIC {factory.code}", Role.LAB, factory, "Laboratory")
            lab_mgr = make(f"{code}.labmgr", "Sample", f"Lab manager {factory.code}", Role.PIC, factory, "Laboratory")
            if factory.factory_manager_id is None:
                factory.factory_manager = fm
                factory.save()
            for user, designation in ((pic, "Production PIC"), (lab, "Lab PIC"), (lab_mgr, "Lab manager")):
                FactoryPIC.objects.get_or_create(factory=factory, user=user, defaults={"designation": designation})
        self.stdout.write(f"Demo users created. Password for every demo user: {DEMO_PASSWORD}")
