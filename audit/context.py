from contextvars import ContextVar

_current_actor = ContextVar("audit_current_actor", default=None)


def get_actor():
    return _current_actor.get()


def set_actor(user):
    return _current_actor.set(user if getattr(user, "is_authenticated", False) else None)


def reset_actor(token):
    _current_actor.reset(token)
