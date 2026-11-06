"""Lists the MR maintains without IT (proposal sections 5.1, 5.2, 5.3 and 5.4)."""

from django.core.validators import MinValueValidator
from django.db import models


class ComplaintType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    requires_details = models.BooleanField(
        default=False, help_text="The lodger must enter short details, as for Others."
    )
    reminder_days = models.PositiveSmallIntegerField(
        default=3,
        validators=[MinValueValidator(1)],
        help_text="A reminder is sent when an action has not been done within this many days.",
    )
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class ComplaintSubType(models.Model):
    complaint_type = models.ForeignKey(ComplaintType, on_delete=models.CASCADE, related_name="sub_types")
    name = models.CharField(max_length=100)
    requires_details = models.BooleanField(
        default=False, help_text="The lodger must enter short details, as for an Others sub-type."
    )
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["complaint_type__sort_order", "sort_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["complaint_type", "name"], name="unique_subtype_per_type")
        ]

    def __str__(self):
        return self.name


class LabTest(models.Model):
    """A row in the laboratory test grid (FFA, M&I, PV, IV, Colour, Appearance and odour)."""

    name = models.CharField(max_length=100, unique=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class ComplaintMethod(models.Model):
    """How a complaint was received. The choices are to be confirmed (open point 6)."""

    name = models.CharField(max_length=100, unique=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name
