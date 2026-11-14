from django.conf import settings
from django.contrib.auth.models import AbstractUser, Group
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

COORDINATOR_GROUP = "Coordinator (MR)"


class Role(models.TextChoices):
    LODGER = "lodger", "Lodger"
    PIC = "pic", "PIC"
    LAB = "lab", "Laboratory staff"
    HOD = "hod", "Head of Department"
    FM = "fm", "Factory Manager"
    COORDINATOR = "coordinator", "Coordinator (MR)"
    MANAGEMENT = "management", "Top management"
    TRADER = "trader", "Trader / commercial"


class Department(models.Model):
    """A department that investigates complaints (QA, Production, Operation, Shipping). Complaints are routed by it."""

    name = models.CharField(max_length=60, unique=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Factory(models.Model):
    """One of the group's factories. The code prefixes complaint reference numbers."""

    code = models.CharField(max_length=4, unique=True, help_text="For example FA, used in FA/2026/0001.")
    name = models.CharField(max_length=100)
    factory_manager = models.ForeignKey(
        "User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        limit_choices_to={"role": Role.FM},
        help_text="The default FM for complaints of this factory.",
    )

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "factories"

    def __str__(self):
        return self.name


class User(AbstractUser):
    email = models.EmailField("email address", unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.LODGER)
    factory = models.ForeignKey(
        Factory,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="users",
        help_text="Leave empty for group-level users (Coordinator, top management, "
        "or a lodger outside the factories).",
    )
    department = models.CharField(max_length=100, blank=True)
    deputy = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deputy_for",
        help_text="Acts for this user while they are away.",
    )
    away_until = models.DateField(
        null=True, blank=True, help_text="The user is treated as away up to and including this date."
    )
    assigned_factories = models.ManyToManyField(
        Factory,
        blank=True,
        related_name="assigned_users",
        help_text="More factories this user covers, besides the one above: a trader who handles customers of "
        "several factories, or a group PIC (for example Shipping) who handles all of them.",
    )
    dept = models.ForeignKey(
        Department,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="users",
        verbose_name="Department (for routing)",
        help_text="The department a PIC or HOD belongs to. A complaint routed to a department goes to its PICs and HOD.",
    )

    class Meta:
        ordering = ["username"]

    def __str__(self):
        return self.get_full_name() or self.username

    def clean(self):
        super().clean()
        # A new factory user needs a factory; one who already covers several factories may leave this empty.
        if self.role in FACTORY_ROLES and not self.factory_id and not (self.pk and self.assigned_factories.exists()):
            raise ValidationError({"factory": "This role belongs to a factory."})
        if self.role in GROUP_ROLES and self.factory_id:
            raise ValidationError({"factory": "Group-level roles do not belong to a factory."})
        if self.deputy_id and self.deputy_id == self.pk:
            raise ValidationError({"deputy": "A user cannot be their own deputy."})

    @property
    def sees_all_factories(self):
        return self.is_superuser or self.role in GROUP_ROLES

    def factory_ids(self):
        """The factories this user covers: their own and the ones assigned to them (empty for a group-level user)."""
        ids = set(self.assigned_factories.values_list("pk", flat=True)) if self.pk else set()
        if self.factory_id:
            ids.add(self.factory_id)
        return ids

    def is_away(self, today=None):
        today = today or timezone.localdate()
        return self.away_until is not None and today <= self.away_until

    def acting_user(self, today=None):
        """The user who should receive this user's approvals right now."""
        if self.is_away(today) and self.deputy_id and self.deputy.is_active:
            return self.deputy
        return self

    def save(self, *args, **kwargs):
        # The MR maintains the drop-down lists in the admin site, so the Coordinator is staff.
        # Other staff (IT administrators) are superusers.
        if self.dept_id and not self.department:
            self.department = self.dept.name
        if not self.is_superuser:
            self.is_staff = self.role == Role.COORDINATOR
        super().save(*args, **kwargs)
        self._sync_coordinator_group()

    def _sync_coordinator_group(self):
        group = Group.objects.filter(name=COORDINATOR_GROUP).first()
        if group is None:
            return
        if self.role == Role.COORDINATOR:
            self.groups.add(group)
        else:
            self.groups.remove(group)


# Roles that always belong to one factory, and roles that see the whole group.
FACTORY_ROLES = {Role.PIC, Role.LAB, Role.HOD, Role.FM}
GROUP_ROLES = {Role.COORDINATOR, Role.MANAGEMENT}
# Roles that see the complaints of their factories only (one factory, or several assigned ones).
FACTORY_SCOPED_ROLES = FACTORY_ROLES | {Role.TRADER}


class FactoryPIC(models.Model):
    """An entry in the list of PICs the Coordinator picks from when assigning a complaint."""

    factory = models.ForeignKey(Factory, on_delete=models.CASCADE, related_name="pics")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pic_entries",
        limit_choices_to={"role__in": [Role.PIC, Role.LAB]},
    )
    designation = models.CharField(max_length=100, help_text="For example Lab PIC or Lab manager.")
    department = models.ForeignKey(
        Department,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pic_entries",
        help_text="A complaint routed to this department is assigned to its PICs of the factory.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["factory__code", "designation"]
        constraints = [models.UniqueConstraint(fields=["factory", "user"], name="unique_pic_per_factory")]
        verbose_name = "factory PIC"

    def __str__(self):
        return f"{self.factory.name} {self.designation}"

    @property
    def email(self):
        return self.user.email

    def clean(self):
        super().clean()
        if self.user_id and self.factory_id and self.factory_id not in self.user.factory_ids():
            raise ValidationError({"user": "The user does not cover this factory. Assign the factory to the user first."})
