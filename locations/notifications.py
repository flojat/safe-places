"""Sicherheitsbenachrichtigungen an die verifizierte Adresse (Abschnitt 8).

Versand ueber das gemeinsame Mailbudget; Ausfall blockiert die Aktion nicht und
erzeugt keine Wiederholungen. Inhalte ohne Codes, Links oder Geheimnisse.
"""

from __future__ import annotations

import logging

from django.template.loader import render_to_string

from . import rate_limits
from .services import security_mail

logger = logging.getLogger("locations.notifications")

EVENTS = {
    "factor_added": "Ein zusätzlicher Anmeldeschritt wurde eingerichtet",
    "factor_removed": "Ein Anmeldeschritt wurde entfernt",
    "backup_code_used": "Ein Wiederherstellungscode wurde verwendet",
    "password_reset": "Ihr Passwort wurde zurückgesetzt",
    "admin_recovery": "Ihr Konto wurde in die Neueinrichtung versetzt",
}


def notify(user, event: str, request=None) -> None:
    if event not in EVENTS or not user.email or user.email_verified_at is None:
        return
    body = render_to_string("emails/security_notice.txt", {"event_text": EVENTS[event]})
    try:
        security_mail.send(purpose=f"notice-{event}", recipient=user.email, user=user, request=request,
                           subject=f"Sicherheitshinweis: {EVENTS[event]}", body=body)
    except (security_mail.MailBudgetExceeded, rate_limits.RateLimiterUnavailable):
        logger.info("Sicherheitshinweis nicht gesendet (Budget): event=%s", event)
