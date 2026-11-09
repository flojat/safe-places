"""Sicherheitsmails (Verifikation, Login-Code, Reset, Aenderungen) mit gemeinsamem Budget.

- Budget wird VOR dem Versand atomar reserviert (Empfaenger, Konto, Zweck, IP, global).
- Fehlversand zaehlt trotzdem (konservativ), keine automatischen Wiederholungen.
- Versand nur an gespeicherte Adressen; kein frei waehlbarer Empfaenger.
- Logs enthalten weder Adressen noch Codes oder Links.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMessage

from .. import rate_limits
from ..models import normalize_email_address

logger = logging.getLogger("locations.security_mail")


class MailBudgetExceeded(Exception):
    def __init__(self, retry_after: int, scope: str):
        super().__init__(scope)
        self.retry_after = retry_after
        self.scope = scope


def reserve_budget(*, purpose: str, recipient: str, user=None, request=None) -> None:
    """Reserviert genau eine Sicherheitsmail. Wirft MailBudgetExceeded oder
    RateLimiterUnavailable (dann darf nicht versendet werden)."""
    rcpt = normalize_email_address(recipient)
    limits = [
        *rate_limits.configured("mail_recipient", rcpt),
        *rate_limits.configured("mail_purpose_recipient", f"{purpose}:{rcpt}"),
        *rate_limits.configured("mail_global", "global"),
    ]
    if user is not None and user.pk:
        limits += rate_limits.configured("mail_account", str(user.pk))
    if request is not None:
        limits += rate_limits.configured("mail_ip", rate_limits.client_ip(request))
    try:
        rate_limits.reserve(limits)
    except rate_limits.RateLimited as exc:
        logger.info("Sicherheitsmail abgelehnt: purpose=%s scope=%s", purpose, exc.scope)
        raise MailBudgetExceeded(exc.retry_after, exc.scope) from exc


def deliver(*, purpose: str, recipient: str, subject: str, body: str) -> bool:
    """Versendet nach erfolgter Reservierung. Gibt False bei Fehler zurueck."""
    message = EmailMessage(subject=subject, body=body, from_email=settings.DEFAULT_FROM_EMAIL, to=[recipient])
    try:
        message.send(fail_silently=False)
    except Exception as exc:  # SMTP/Netz/Timeout: kein Retry, keine Details ins Log
        logger.warning("Sicherheitsmail nicht zugestellt: purpose=%s fehler=%s", purpose, type(exc).__name__)
        return False
    return True


def send(*, purpose: str, recipient: str, subject: str, body: str, user=None, request=None) -> bool:
    reserve_budget(purpose=purpose, recipient=recipient, user=user, request=request)
    return deliver(purpose=purpose, recipient=recipient, subject=subject, body=body)
