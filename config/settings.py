"""Django settings for the Group Customer Complaint Platform.

Values that differ between development and production are read from
environment variables; the defaults suit local development.
"""

import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).lower() in ("1", "true", "yes")


DEBUG = env_bool("DJANGO_DEBUG", True)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off.")
    SECRET_KEY = "dev-only-insecure-key"

ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if h]
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o]
# Render tells the app its own address; trust it without any setting.
if os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(os.environ["RENDER_EXTERNAL_HOSTNAME"])
if os.environ.get("RENDER_EXTERNAL_URL"):
    CSRF_TRUSTED_ORIGINS.append(os.environ["RENDER_EXTERNAL_URL"])

# A submitted complaint goes straight to the PICs of the department the routing table names (and the Coordinator is
# copied). Turn it off to have every complaint wait for the Coordinator to assign it.
AUTO_ASSIGN = env_bool("AUTO_ASSIGN", True)

# A public demo: the sign-in page lists the demo users and their shared password.
DEMO_MODE = env_bool("DEMO_MODE", False)


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "anymail",
    "accounts",
    "audit",
    "complaints",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "audit.middleware.CurrentActorMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if not DEBUG:
    # Serves the CSS and scripts that `collectstatic` gathered. In development the staticfiles app does it.
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "complaints.context.navigation",
                "complaints.context.demo",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# PostgreSQL in production: either one DATABASE_URL (what Render provides) or the POSTGRES_* variables.
if os.environ.get("DATABASE_URL"):
    DATABASES["default"] = dj_database_url.parse(os.environ["DATABASE_URL"], conn_max_age=60)
elif os.environ.get("POSTGRES_DB"):
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["POSTGRES_DB"],
        "USER": os.environ.get("POSTGRES_USER", ""),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }


AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


LANGUAGE_CODE = "en-gb"
# Assumed from the "Sdn Bhd" names in the proposal; change if the group runs elsewhere.
TIME_ZONE = "Asia/Kuala_Lumpur"
USE_I18N = True
USE_TZ = True


STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"  # `manage.py collectstatic` gathers the files here
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        # Hashed, compressed file names in production; plain files while developing and testing.
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
# Uploaded files are served only through views that check access, never straight from MEDIA_ROOT.
ATTACHMENT_MAX_MB = int(os.environ.get("ATTACHMENT_MAX_MB", "10"))

# The address people use to reach the platform, used for the links in emails.
SITE_URL = (os.environ.get("SITE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "http://127.0.0.1:8000").rstrip("/")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Email. In order of preference:
#   1. EMAIL_BACKEND set explicitly;
#   2. an email service reached over HTTPS, chosen by which key is set (Brevo, Resend, SendGrid, Mailgun, Postmark).
#      Use one of these on a host that blocks SMTP, such as Render's free plan;
#   3. SMTP, when EMAIL_HOST is set;
#   4. otherwise emails are only written to the server log: nothing is delivered (development, or a demo).
# DEFAULT_FROM_EMAIL must be an address the service has verified, or it refuses the message.
_ANYMAIL_SERVICES = [
    ("BREVO_API_KEY", "anymail.backends.brevo.EmailBackend"),
    ("RESEND_API_KEY", "anymail.backends.resend.EmailBackend"),
    ("SENDGRID_API_KEY", "anymail.backends.sendgrid.EmailBackend"),
    ("MAILGUN_API_KEY", "anymail.backends.mailgun.EmailBackend"),
    ("POSTMARK_SERVER_TOKEN", "anymail.backends.postmark.EmailBackend"),
]
ANYMAIL = {
    name: os.environ[name]
    for name in ("BREVO_API_KEY", "RESEND_API_KEY", "SENDGRID_API_KEY", "MAILGUN_API_KEY", "MAILGUN_SENDER_DOMAIN", "POSTMARK_SERVER_TOKEN")
    if os.environ.get(name)
}
_service_backend = next((backend for key, backend in _ANYMAIL_SERVICES if os.environ.get(key)), None)

if os.environ.get("EMAIL_BACKEND"):
    EMAIL_BACKEND = os.environ["EMAIL_BACKEND"]
elif _service_backend:
    EMAIL_BACKEND = _service_backend
elif os.environ.get("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
# Used when the SMTP backend is chosen.
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "25"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", False)
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "complaints@example.com")
# Written in the signature of the reply to the customer.
COMPANY_NAME = os.environ.get("COMPANY_NAME", "BEST Group")


# Behind a proxy that ends HTTPS (Render, a load balancer, Nginx) the app is told the original scheme.
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", True)
    SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]  # the platform's health check is plain HTTP
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Turn HSTS on (for example 31536000) once HTTPS has been working for a while: browsers remember it.
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "0"))

# Errors go to the console, where the host (Render, Docker, systemd) collects them. Without this, Django
# sends server errors only to ADMINS by email, so a failure would leave no trace in the logs.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO")},
}
