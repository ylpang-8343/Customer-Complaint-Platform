from . import scheduler


class AutoReminderMiddleware:
    """After each response, give the automatic reminder check a chance to start (see ``scheduler``)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        scheduler.tick()
        return response
