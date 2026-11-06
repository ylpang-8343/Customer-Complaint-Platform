"""Factory-scoped access rules (proposal section 5.4, "Access").

The Coordinator and top management see every factory. Factory staff (PIC, laboratory,
HOD, FM) see their own factory's complaints plus any they lodged themselves. A plain
lodger, who may sit outside the factories, sees only the complaints they lodged.
"""

from django.db.models import Q

from .models import Factory, Role

FACTORY_STAFF_ROLES = {Role.PIC, Role.LAB, Role.HOD, Role.FM}


def factories_for(user):
    """Factories whose complaints the user may see as a whole."""
    if not user.is_authenticated:
        return Factory.objects.none()
    if user.sees_all_factories:
        return Factory.objects.all()
    if user.role in FACTORY_STAFF_ROLES and user.factory_id:
        return Factory.objects.filter(pk=user.factory_id)
    return Factory.objects.none()


def scope_to_user(queryset, user, factory_field="factory", lodger_field="lodged_by"):
    """Limit a queryset of factory-owned records to what the user may see."""
    if not user.is_authenticated:
        return queryset.none()
    if user.sees_all_factories:
        return queryset
    own = Q(**{lodger_field: user})
    if user.role in FACTORY_STAFF_ROLES and user.factory_id:
        return queryset.filter(Q(**{factory_field: user.factory_id}) | own)
    return queryset.filter(own)
