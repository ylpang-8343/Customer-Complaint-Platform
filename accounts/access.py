"""Factory-scoped access rules (proposal section 5.4, "Access").

The Coordinator and top management see every factory. Factory staff (PIC, laboratory,
HOD, FM) see their own factory's complaints plus any they lodged themselves. A plain
lodger, who may sit outside the factories, sees only the complaints they lodged.
"""

from django.db.models import Q

from .models import Factory, Role

FACTORY_STAFF_ROLES = {Role.PIC, Role.LAB, Role.HOD, Role.FM, Role.TRADER}  # see the factories they cover


def factories_for(user):
    """Factories whose complaints the user may see as a whole."""
    if not user.is_authenticated:
        return Factory.objects.none()
    if user.sees_all_factories:
        return Factory.objects.all()
    if user.role in FACTORY_STAFF_ROLES:
        return Factory.objects.filter(pk__in=user.factory_ids())
    return Factory.objects.none()


def scope_to_user(queryset, user, factory_field="factory", lodger_field="lodged_by"):
    """Limit a queryset of factory-owned records to what the user may see."""
    if not user.is_authenticated:
        return queryset.none()
    if user.sees_all_factories:
        return queryset
    own = Q(**{lodger_field: user})
    if user.role in FACTORY_STAFF_ROLES:
        factory_ids = user.factory_ids()
        if factory_ids:
            return queryset.filter(Q(**{f"{factory_field}__in": factory_ids}) | own)
    return queryset.filter(own)
