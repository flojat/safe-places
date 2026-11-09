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
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        "axes": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
}

# Private Testmedien in eigenem temporaeren Verzeichnis (nie im Entwicklungs-var/).
import tempfile as _tempfile  # noqa: E402

PRIVATE_MEDIA_ROOT = _tempfile.mkdtemp(prefix="adresskarte-test-media-")

# Tile-Cache und weitere Laufzeitdaten der Tests ebenfalls temporaer (nie im Entwicklungs-var/).
from pathlib import Path as _Path  # noqa: E402

VAR_DIR = _Path(_tempfile.mkdtemp(prefix="adresskarte-test-var-"))

# Keine echten swisstopo-Abrufe in automatisierten Tests (Provider wird gemockt).
SWISSTOPO_NETWORK_ENABLED = False

# Temporaere Testverzeichnisse beim Prozessende entfernen.
import atexit as _atexit  # noqa: E402
import shutil as _shutil  # noqa: E402

_atexit.register(_shutil.rmtree, PRIVATE_MEDIA_ROOT, True)
_atexit.register(_shutil.rmtree, str(VAR_DIR), True)

# Tests unabhaengig von der lokalen .env-Kartenquelle: lokale Testkacheln.
MAP_TILE_SOURCE = "local_test"
REGISTRATION_REQUIRES_ADMIN = False
