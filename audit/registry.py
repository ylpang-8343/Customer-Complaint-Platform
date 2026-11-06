"""Record changes to registered models in the audit log.

Call ``register(Model)`` once, from the app's ``ready()``. Saves and deletes made through
the ORM are then logged with the user of the current request, or with no user for
management commands and scheduled jobs.
"""

from django.contrib.contenttypes.models import ContentType
from django.db.models.fields.files import FieldFile
from django.db.models.signals import post_delete, post_save, pre_save

from .context import get_actor

_registered = {}


def register(model, exclude=()):
    if model in _registered:
        return
    _registered[model] = {f.attname for f in model._meta.concrete_fields} - set(exclude)
    uid = f"audit-{model._meta.label}"
    pre_save.connect(_remember_old_values, sender=model, dispatch_uid=f"{uid}-pre")
    post_save.connect(_log_save, sender=model, dispatch_uid=f"{uid}-post")
    post_delete.connect(_log_delete, sender=model, dispatch_uid=f"{uid}-del")


def _plain(value):
    # A file field holds a FieldFile; the log keeps just its stored name.
    return value.name if isinstance(value, FieldFile) else value


def _values(instance, fields):
    return {name: _plain(getattr(instance, name)) for name in fields}


def _write(instance, action, changes):
    from .models import AuditLog

    actor = get_actor()
    AuditLog.objects.create(
        actor=actor,
        actor_name=(actor.get_full_name() or actor.get_username()) if actor else "",
        action=action,
        content_type=ContentType.objects.get_for_model(type(instance)),
        object_id=str(instance.pk),
        object_repr=str(instance)[:255],
        changes=changes,
    )


def _remember_old_values(sender, instance, raw=False, **kwargs):
    instance._audit_old = None
    if raw or instance.pk is None:
        return
    fields = _registered[sender]
    instance._audit_old = sender._default_manager.filter(pk=instance.pk).values(*fields).first()


def _log_save(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    fields = _registered[sender]
    new = _values(instance, fields)
    old = getattr(instance, "_audit_old", None)
    if created or old is None:
        _write(instance, "created", {name: [None, value] for name, value in new.items()})
        return
    changes = {name: [old[name], value] for name, value in new.items() if old[name] != value}
    if changes:
        _write(instance, "updated", changes)


def _log_delete(sender, instance, **kwargs):
    fields = _registered[sender]
    _write(instance, "deleted", {name: [value, None] for name, value in _values(instance, fields).items()})
