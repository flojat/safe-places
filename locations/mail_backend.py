"""SMTP-Backend mit festem TLS-Mindeststandard und eng begrenzten Kompatibilitaetsprofilen.

Zertifikats- und Hostnamenpruefung sind IMMER aktiv (kein Abschalten moeglich).
EMAIL_TLS_PROFILE:
  default    OpenSSL-Standard (Sicherheitsstufe des Systems), TLS >= 1.2
  no_dhe     wie default, aber ohne klassisches DHE (nur ECDHE); behebt "DH key too small"
             bei Servern mit schwachen DH-Parametern OHNE Sicherheitsabsenkung
  legacy_dh  Sicherheitsstufe 1 nur fuer SMTP (akzeptiert 1024-Bit-DH); bewusste, dokumentierte
             Abschwaechung fuer einen nicht selbst betriebenen MX; doctor meldet WARN
EMAIL_TLS_CAFILE (optional): eigene CA fuer einen internen MX statt Abschalten der Pruefung.
"""

from __future__ import annotations

import ssl

from django.conf import settings
from django.core.mail.backends import smtp
from django.utils.functional import cached_property

PROFILES = ("default", "no_dhe", "legacy_dh")
_NO_DHE = "ECDHE+AESGCM:ECDHE+CHACHA20:ECDHE+AES:!aNULL:!eNULL:!MD5:!DSS:!RC4:!3DES:!kDHE"


def build_context(profile: str, cafile: str | None = None) -> ssl.SSLContext:
    if profile not in PROFILES:
        raise ValueError("Unbekanntes EMAIL_TLS_PROFILE")
    context = ssl.create_default_context(cafile=cafile or None)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if profile == "no_dhe":
        context.set_ciphers(_NO_DHE)
    elif profile == "legacy_dh":
        context.set_ciphers("DEFAULT:@SECLEVEL=1")
    # Unveraenderlich: Zertifikat und Hostname werden geprueft.
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


class EmailBackend(smtp.EmailBackend):
    @cached_property
    def ssl_context(self):
        context = build_context(getattr(settings, "EMAIL_TLS_PROFILE", "default"),
                                getattr(settings, "EMAIL_TLS_CAFILE", "") or None)
        if self.ssl_certfile or self.ssl_keyfile:
            context.load_cert_chain(self.ssl_certfile, self.ssl_keyfile)
        return context
