from .context import reset_actor, set_actor


class CurrentActorMiddleware:
    """Make the signed-in user available to the audit log for the length of a request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = set_actor(request.user)
        try:
            return self.get_response(request)
        finally:
            reset_actor(token)
