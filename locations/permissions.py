"""Zentrale Policies. Staffstatus, is_authenticated oder eine ID ersetzen sie nicht.

Anmeldezustaende (Abschnitt 8):
  anonymous                      kein Login
  pending_email                  Konto ohne bestaetigte E-Mail / nicht aktiviert
  password_verified_mfa_pending  Passwort geprueft, zweiter Schritt fehlt
  fully_verified                 aktives Konto, bestaetigte E-Mail, OTP-Sitzung
"""

from __future__ import annotations

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect, render

ANONYMOUS = "anonymous"
PENDING_EMAIL = "pending_email"
MFA_PENDING = "password_verified_mfa_pending"
FULLY_VERIFIED = "fully_verified"


def auth_state(request) -> str:
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return ANONYMOUS
    if not user.is_active or user.state != user.State.ACTIVE or user.email_verified_at is None:
        return PENDING_EMAIL
    if not (hasattr(user, "is_verified") and user.is_verified()):
        return MFA_PENDING
    return FULLY_VERIFIED


def is_fully_verified(request) -> bool:
    return auth_state(request) == FULLY_VERIFIED


def can_access_member_content(request) -> bool:
    """Mitgliedsrechte: fuer alle Rollen gleich, inklusive Superuser."""
    return is_fully_verified(request)


def fully_verified_required(view):
    """Schuetzt Mitglieds-/Kontoverwaltungs-/Arbeitsrouten."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        state = auth_state(request)
        if state == FULLY_VERIFIED:
            return view(request, *args, **kwargs)
        if state == ANONYMOUS:
            return redirect_to_login(request.get_full_path())
        if state == MFA_PENDING:
            return redirect("two_factor:setup")
        response = render(request, "account/not_activated.html", status=403)
        response["Cache-Control"] = "private, no-store"
        return response

    return wrapper


# --- Rollenbezogene Policies (Etappe 4) ---------------------------------------
# Jede Pruefung: vollstaendig verifizierte Sitzung + aktuelle Rolle + aktuelle Zuordnung.


def can_use_owner_portal(request) -> bool:
    from .roles import is_object_manager

    return is_fully_verified(request) and is_object_manager(request.user)


def can_manage_location(request, location) -> bool:
    return can_use_owner_portal(request) and location.managed_by_id == request.user.pk


def can_manage_provider(request, provider) -> bool:
    return can_use_owner_portal(request) and provider.managed_by_id == request.user.pk


def can_publish(request) -> bool:
    from .roles import is_admin, is_editor

    return is_fully_verified(request) and (is_editor(request.user) or is_admin(request.user))


def can_administer(request) -> bool:
    from .roles import is_admin

    return is_fully_verified(request) and is_admin(request.user)
