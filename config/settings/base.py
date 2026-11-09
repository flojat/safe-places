"""Gemeinsame Einstellungen. Umgebungsspezifisch: local.py, test.py, production.py.

Werte aus der privaten .env (Format siehe config/envfile.py). Keine Geheimnisse
in diesem Modul.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from config.envfile import Env

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Pfad zur privaten Konfiguration; auf dem Hosting ausserhalb des Releases (shared/config/.env).
ENV_FILE = Path(os.environ.get("ADRESSKARTE_ENV_FILE", BASE_DIR / ".env"))
ENV = Env(ENV_FILE)

ENVIRONMENT = ENV.get("ENVIRONMENT", "local")
SECRET_KEY = ENV.require("DJANGO_SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS: list[str] = []

VAR_DIR = (BASE_DIR / ENV.get("VAR_DIR", "var")).resolve()
PRIVATE_MEDIA_ROOT = VAR_DIR / "private_media"
PRIVATE_MAIL_DIR = VAR_DIR / "private_mail"
TILE_CACHE_DIR = VAR_DIR / "tile_cache"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_otp",
    "django_otp.plugins.otp_static",
    "django_otp.plugins.otp_totp",
    "django_otp.plugins.otp_email",
    "two_factor",
    "two_factor.plugins.email",
    "axes",
    "locations",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # django-axes muss als letzte Middleware stehen.
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

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
            ],
        },
    },
]


def mysql_database(prefix: str = "") -> dict:
    """MariaDB/MySQL-Verbindung aus DB_* bzw. TEST_DB_*. Kein SQLite-Fallback."""
    return {
        "ENGINE": "django.db.backends.mysql",
        "HOST": ENV.get(f"{prefix}DB_HOST", "localhost"),
        "PORT": ENV.get(f"{prefix}DB_PORT", ""),
        "NAME": ENV.require(f"{prefix}DB_NAME"),
        "USER": ENV.require(f"{prefix}DB_USER"),
        "PASSWORD": ENV.require(f"{prefix}DB_PASSWORD"),
        "CONN_MAX_AGE": 0,
        "ATOMIC_REQUESTS": False,
        "OPTIONS": {
            "charset": "utf8mb4",
            # Nur Sitzungswerte, keine globalen Servereinstellungen.
            "init_command": (
                "SET SESSION sql_mode='STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,"
                "NO_ENGINE_SUBSTITUTION', SESSION innodb_strict_mode=1, "
                "SESSION default_storage_engine=InnoDB"
            ),
            "isolation_level": "read committed",
        },
    }


DATABASES = {"default": mysql_database()}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "locations.User"
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "two_factor:login"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# Sitzungen (Abschnitt 8): Startwerte, konfigurierbar.
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 12 * 3600
MEMBER_SESSION_IDLE = timedelta(minutes=ENV.get_int("MEMBER_SESSION_IDLE_MINUTES", 60))
MEMBER_SESSION_ABSOLUTE = timedelta(hours=ENV.get_int("MEMBER_SESSION_ABSOLUTE_HOURS", 12))
WORK_SESSION_IDLE = timedelta(minutes=ENV.get_int("WORK_SESSION_IDLE_MINUTES", 30))
WORK_SESSION_ABSOLUTE = timedelta(hours=ENV.get_int("WORK_SESSION_ABSOLUTE_HOURS", 8))
REAUTH_WINDOW = timedelta(minutes=ENV.get_int("REAUTH_WINDOW_MINUTES", 5))
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"

# django-otp E-Mail-Code (Abschnitt 8): 5 Minuten gueltig, 60 s Cooldown.
OTP_EMAIL_TOKEN_VALIDITY = 300
OTP_EMAIL_COOLDOWN_DURATION = 60
OTP_EMAIL_THROTTLE_FACTOR = 1
OTP_EMAIL_SUBJECT = "Ihr Anmeldecode"
OTP_TOTP_ISSUER = "Adresskarte"
# Kein "Diesen Browser merken": TWO_FACTOR_REMEMBER_COOKIE_AGE bleibt bewusst ungesetzt.
TWO_FACTOR_PATCH_ADMIN = False

# django-axes: Passwortfehlversuche.
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=15)
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = False
AXES_USERNAME_FORM_FIELD = "auth-username"
AXES_ENABLE_ACCESS_FAILURE_LOG = False
AXES_SENSITIVE_PARAMETERS = ["password", "auth-password", "token", "otp_token"]

DEFAULT_FROM_EMAIL = ENV.get("DEFAULT_FROM_EMAIL", "noreply@example.invalid")
EMAIL_TIMEOUT = 10

LANGUAGE_CODE = "de-ch"
TIME_ZONE = "Europe/Zurich"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = (BASE_DIR / ENV.get("STATIC_ROOT", "staticfiles")).resolve()
# Bewusst kein MEDIA_URL: private Medien nur ueber kontrollierte Views.

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
}

# Karte (Abschnitt 5). Zoomgrenzen vor Etappe 5 gegen die swisstopo-Doku pruefen.
MAP_TILE_SOURCE = ENV.get("MAP_TILE_SOURCE", "local_test")  # local_test | swisstopo (Etappe 5)
MAP_MIN_ZOOM = 7
MAP_MAX_ZOOM = 18
