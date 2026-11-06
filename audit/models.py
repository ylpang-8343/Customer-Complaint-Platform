from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models


class AuditLog(models.Model):
    """Who changed what and when (proposal section 5.4, "Records"). Entries are append-only."""

    class Action(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        DELETED = "deleted", "Deleted"

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_name = models.CharField(max_length=150, blank=True, help_text="Kept in case the user is removed.")
    action = models.CharField(max_length=10, choices=Action.choices)
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    object_id = models.CharField(max_length=64)
    object_repr = models.CharField(max_length=255)
    # {"field": [old, new]}
    changes = models.JSONField(default=dict, encoder=DjangoJSONEncoder)

    class Meta:
        ordering = ["-timestamp", "-id"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.actor_name or 'system'} {self.action} {self.object_repr}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("Audit log entries cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit log entries cannot be deleted.")
