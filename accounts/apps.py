from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        from audit.registry import register

        from .models import Factory, FactoryPIC, User

        register(Factory)
        register(FactoryPIC)
        # Passwords and login times are not business changes.
        register(User, exclude=("password", "last_login"))
