"""Automatisierte Tests auf der separaten MariaDB-Testdatenbank.

Aufruf: python manage.py test --settings=config.settings.test --keepdb

Schutz gegen Zielverwechslung: Tests laufen nur mit eigenem TEST_DB_*-Konto
und einem Datenbanknamen, der sich von DB_NAME unterscheidet. Die Testdaten-
bank wird vom Betreiber bereitgestellt; Django verwendet sie direkt (TEST.NAME).
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import ENV, ENVIRONMENT, mysql_database

if ENVIRONMENT == "production":
    raise ImproperlyConfigured("Tests sind mit ENVIRONMENT=production verboten.")

_test_db = mysql_database("TEST_")
_dev_name = ENV.get("DB_NAME")
if _dev_name and _dev_name == _test_db["NAME"]:
    raise ImproperlyConfigured("TEST_DB_NAME darf nicht DB_NAME entsprechen.")
if ENV.get("DB_USER") and ENV.get("DB_USER") == _test_db["USER"]:
    raise ImproperlyConfigured("Testkonto muss vom Entwicklungs-/Produktivkonto getrennt sein.")

_test_db["TEST"] = {
    "NAME": _test_db["NAME"],
    "CHARSET": "utf8mb4",
    "COLLATION": "utf8mb4_unicode_ci",
}
DATABASES = {"default": _test_db}

DEBUG = False
ALLOWED_HOSTS = ["testserver", "127.0.0.1", "localhost"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # nur Testgeschwindigkeit

# Erwartete 400/404-Antworten in Tests nicht als Warnungen ausgeben.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False}},
    "root": {"handlers": ["console"], "level": "WARNING"},
}
