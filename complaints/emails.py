"""Emails sent by the platform. Each function returns the number of emails sent."""

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

from accounts.models import Role, User

from .summary import summary_sections


def complaint_url(complaint):
    return settings.SITE_URL + reverse("complaint_detail", args=[complaint.pk])


def coordinator_emails():
    return list(
        User.objects.filter(role=Role.COORDINATOR, is_active=True)
        .exclude(email="")
        .values_list("email", flat=True)
    )


def notify_coordinator_of_submission(complaint):
    """Step 2: when the form is submitted, the platform emails the Coordinator."""
    recipients = coordinator_emails()
    if not recipients:
        return 0
    body = render_to_string(
        "email/complaint_submitted.txt",
        {"complaint": complaint, "sections": summary_sections(complaint), "url": complaint_url(complaint)},
    )
    return send_mail(
        subject=f"Customer complaint {complaint.reference} submitted for assignment",
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipients,
    )
