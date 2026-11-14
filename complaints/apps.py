from django.apps import AppConfig


class ComplaintsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "complaints"

    def ready(self):
        from audit.registry import register

        from . import models

        for model in (
            models.ComplaintType,
            models.ComplaintSubType,
            models.LabTest,
            models.ComplaintMethod,
            models.Complaint,
            models.Attachment,
            models.Assignment,
            models.Investigation,
            models.LabSection,
            models.LabResult,
            models.Scar,
            models.CustomerReply,
            models.Verification,
            models.ImportBatch,
            models.Customer,
            models.Product,
            models.PackSize,
        ):
            register(model)
