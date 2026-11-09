"""Lokale Entwicklung: nur Loopback, private Datei-Mailablage, keine echte Mail."""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import ENVIRONMENT, PRIVATE_MAIL_DIR

if ENVIRONMENT != "local":
    raise ImproperlyConfigured("config.settings.local verlangt ENVIRONMENT=local.")

DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

# Kein Console-Backend: Aktivierungslinks/OTPs sollen nicht in Terminal-/Agentenprotokollen landen.
EMAIL_BACKEND = "django.core.mail.backends.filebased.EmailBackend"
EMAIL_FILE_PATH = str(PRIVATE_MAIL_DIR)
