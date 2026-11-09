"""Sitzungssicherheit: Kontozustand, Sicherheitsgeneration und Zeitlimits je Anfrage."""

from __future__ import annotations

import time

from django.conf import settings
from django.contrib.auth import logout

SESSION_GENERATION = "acct_generation"
SESSION_LOGIN_AT = "acct_login_at"
SESSION_LAST_SEEN = "acct_last_seen"
_LAST_SEEN_WRITE_INTERVAL = 30

# Einrichtungszustand (Passwort ok, zweiter Schritt fehlt): kurz halten.
SETUP_IDLE_SECONDS = 15 * 60
SETUP_ABSOLUTE_SECONDS = 60 * 60


def init_session(request, user) -> None:
    # Gastsitzungs-Laufzeit nicht in die angemeldete Sitzung uebernehmen.
    request.session.pop("guest_started", None)
    request.session.set_expiry(None)
    now = int(time.time())
    request.session[SESSION_GENERATION] = user.security_generation
    request.session[SESSION_LOGIN_AT] = now
    request.session[SESSION_LAST_SEEN] = now


def _limits(user) -> tuple[int, int]:
    if not (hasattr(user, "is_verified") and user.is_verified()):
        return SETUP_IDLE_SECONDS, SETUP_ABSOLUTE_SECONDS
    if user.is_staff or user.is_superuser or user.groups.filter(name="Objektbetreuung").exists():
        return (int(settings.WORK_SESSION_IDLE.total_seconds()),
                int(settings.WORK_SESSION_ABSOLUTE.total_seconds()))
    return (int(settings.MEMBER_SESSION_IDLE.total_seconds()),
            int(settings.MEMBER_SESSION_ABSOLUTE.total_seconds()))


class AccountSecurityMiddleware:
    """Beendet Sitzungen bei Sperre, Generationswechsel (Faktor-/Sicherheitsaenderung)
    oder Ueberschreitung der Leerlauf-/Gesamtdauer. Wirkt ab der naechsten Anfrage."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            self._check(request, user)
        return self.get_response(request)

    def _check(self, request, user) -> None:
        session = request.session
        now = int(time.time())
        generation = session.get(SESSION_GENERATION)
        if (
            generation is None
            or generation != user.security_generation
            or not user.is_active
            or user.state == user.State.SUSPENDED
        ):
            logout(request)
            return
        idle, absolute = _limits(user)
        last_seen = session.get(SESSION_LAST_SEEN, now)
        login_at = session.get(SESSION_LOGIN_AT, now)
        if now - last_seen > idle or now - login_at > absolute:
            logout(request)
            return
        if now - last_seen >= _LAST_SEEN_WRITE_INTERVAL:
            session[SESSION_LAST_SEEN] = now
