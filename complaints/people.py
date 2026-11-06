"""Who is responsible for a complaint at each step, and who is told about it.

A HOD or FM who is away (``away_until``) is replaced by their deputy: the deputy may act, and
the emails go to the deputy instead.
"""

from accounts.models import Factory, Role, User

from .models import Assignment, Complaint


def _unique(users):
    seen, result = set(), []
    for user in users:
        if user is not None and user.pk not in seen:
            seen.add(user.pk)
            result.append(user)
    return result


def coordinators():
    return list(User.objects.filter(role=Role.COORDINATOR, is_active=True))


def is_coordinator(user):
    return user.is_authenticated and user.role == Role.COORDINATOR


def pics(complaint):
    """The people the Coordinator assigned."""
    return [a.user for a in complaint.assignments.select_related("user") if a.user.is_active]


def is_assigned(user, complaint):
    return user.is_authenticated and complaint.assignments.filter(user=user).exists()


def is_lab_staff(user, complaint):
    return is_assigned(user, complaint) and user.role == Role.LAB


def _hods(factory_id):
    return User.objects.filter(role=Role.HOD, factory_id=factory_id, is_active=True)


def hod_factories(user):
    """Factories whose HOD role the user holds, directly or as the deputy of a HOD who is away."""
    ids = set()
    if user.role == Role.HOD and user.factory_id:
        ids.add(user.factory_id)
    for hod in User.objects.filter(role=Role.HOD, deputy=user, is_active=True):
        if hod.is_away():
            ids.add(hod.factory_id)
    return ids


def hods(complaint):
    """The HODs to ask: every HOD of the factory, or the deputy of one who is away."""
    return _unique(hod.acting_user() for hod in _hods(complaint.factory_id))


def _fm_of(factory):
    if factory.factory_manager_id:
        return [factory.factory_manager]
    return list(User.objects.filter(role=Role.FM, factory=factory, is_active=True))


def fm_factories(user):
    """Factories whose FM role the user holds, directly or as the deputy of an FM who is away."""
    ids = set()
    for factory in Factory.objects.select_related("factory_manager"):
        for fm in _fm_of(factory):
            if fm.pk == user.pk or (fm.is_away() and fm.deputy_id == user.pk):
                ids.add(factory.pk)
    return ids


def fms(complaint):
    return _unique(fm.acting_user() for fm in _fm_of(complaint.factory))


def can_act_as_hod(user, complaint):
    return complaint.factory_id in hod_factories(user)


def can_act_as_fm(user, complaint):
    return complaint.factory_id in fm_factories(user)


STATUS = Complaint.Status


def responsible_role(complaint):
    """A short phrase for who the complaint is waiting for, or '' when nobody is."""
    if complaint.is_deleted:
        return ""
    return {
        STATUS.SUBMITTED: "Coordinator",
        STATUS.INVESTIGATION: "PIC",
        STATUS.PENDING_SCAR: "Supplier's SCAR (PIC to follow up)",
        STATUS.HOD_REVIEW: "HOD",
        STATUS.FM_REVIEW: "FM",
        STATUS.APPROVED: "PIC (reply to customer)",
        STATUS.RESPONSE_SENT: "Coordinator (effectiveness check)",
    }.get(complaint.status, "")


def responsible_users(complaint):
    """The people who have to act next."""
    if complaint.is_deleted:
        return []
    status = complaint.status
    if status in (STATUS.SUBMITTED, STATUS.RESPONSE_SENT):
        return coordinators()
    if status in (STATUS.INVESTIGATION, STATUS.PENDING_SCAR, STATUS.APPROVED):
        return pics(complaint)
    if status == STATUS.HOD_REVIEW:
        return hods(complaint)
    if status == STATUS.FM_REVIEW:
        return fms(complaint)
    return []


def assigned_complaint_ids(user):
    return Assignment.objects.filter(user=user).values("complaint_id")
