"""Produktivbetrieb hinter Apache (WSGI-Profil noch durch den Betreiber zu bestaetigen).

Kein Fallback auf lokale Werte: fehlende Pflichtwerte brechen den Start ab.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import ENV, ENVIRONMENT

if ENVIRONMENT != "production":
    raise ImproperlyConfigured("config.settings.production verlangt ENVIRONMENT=production.")

DEBUG = False
ALLOWED_HOSTS = [h.strip() for h in ENV.require("ALLOWED_HOSTS").split(",") if h.strip()]
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS muss explizite Hostnamen enthalten.")
CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS]

SECURE_SSL_REDIRECT = ENV.get_bool("SECURE_SSL_REDIRECT", True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = ENV.get_int("SECURE_HSTS_SECONDS", 0)  # vorsichtig erst nach HTTPS-Pruefung erhoehen
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False

# Nur eine ausdruecklich bestaetigte Proxykette; sonst keine Forwarded-Header vertrauen.
if ENV.get_bool("TRUST_X_FORWARDED_PROTO", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

EMAIL_BACKEND = ENV.get("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
if EMAIL_BACKEND != "django.core.mail.backends.smtp.EmailBackend":
    raise ImproperlyConfigured("Produktiv ist nur das SMTP-Mailbackend zulaessig.")
EMAIL_HOST = ENV.require("EMAIL_HOST")
EMAIL_PORT = ENV.get_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = ENV.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = ENV.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = ENV.get_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = ENV.get_bool("EMAIL_USE_SSL", False)
if not (EMAIL_USE_TLS or EMAIL_USE_SSL):
    raise ImproperlyConfigured("SMTP ohne TLS ist nicht zulaessig.")
DEFAULT_FROM_EMAIL = ENV.require("DEFAULT_FROM_EMAIL")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
OTP_EMAIL_SENDER = DEFAULT_FROM_EMAIL

if not REGISTRATION_REQUIRES_ADMIN:  # noqa: F405
    raise ImproperlyConfigured("Produktiv muss die Registrierung bis zum aktiven Administrator geschlossen sein.")
