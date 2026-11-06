from django.apps import AppConfig


class ComplaintsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "complaints"

    def ready(self):
        from audit.registry import register

        from .models import Attachment, Complaint, ComplaintMethod, ComplaintSubType, ComplaintType, LabTest

        for model in (ComplaintType, ComplaintSubType, LabTest, ComplaintMethod, Complaint, Attachment):
            register(model)
