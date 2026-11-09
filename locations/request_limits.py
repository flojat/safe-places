"""Request-Limits fuer Besucherwege (Abschnitt 8): Sitzung/Konto plus IP.

Gaeste (und nicht vollstaendig angemeldete Konten) werden ueber eine technisch
notwendige, kurzlebige Gastsitzung begrenzt; vollstaendig angemeldete Konten
ueber das Konto (alle Sitzungen teilen sich das Kontingent). Die IP-Grenze gilt
zusaetzlich fuer alle Besucher. Statische Dateien, Login, Registrierung und
Hilfe zaehlen nicht dazu.
"""

from __future__ import annotations

import time
from functools import wraps

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render

from . import rate_limits
from .permissions import is_fully_verified

GUEST_SESSION_KEY = "guest_started"

KINDS = {
    # kind: (Gast-Sitzungslimit oder None=verboten, Mitglied-Kontolimit, IP-Limit)
    "query": ("query_guest_session", "query_member_account", "query_ip"),
    "photo": ("photo_guest_session", "photo_member_account", "photo_ip"),
    "tile": ("tile_guest_session", "tile_member_account", "tile_ip"),
    "detail": (None, "detail_member_account", "detail_ip"),
}


class SessionRequired(Exception):
    pass


def ensure_guest_session(request) -> bool:
    """Legt bei HTML-Seitenaufrufen eine kurzlebige Gastsitzung an (IP-begrenzt).

    Gibt False zurueck, wenn keine neue Sitzung angelegt werden darf; die Seite
    bleibt trotzdem nutzbar, Datenabrufe werden dann abgelehnt.
    """
    if request.user.is_authenticated or request.session.get(GUEST_SESSION_KEY):
        return True
    try:
        rate_limits.reserve(rate_limits.configured("guest_session_ip", rate_limits.client_ip(request)))
    except (rate_limits.RateLimited, rate_limits.RateLimiterUnavailable):
        return False
    request.session[GUEST_SESSION_KEY] = int(time.time())
    request.session.set_expiry(settings.GUEST_SESSION_SECONDS)
    if not request.session.session_key:
        request.session.save()  # Schluessel sofort, damit Direktlinks im selben Request zaehlen
    return True


def check(request, kind: str) -> None:
    """Wirft RateLimited, RateLimiterUnavailable oder SessionRequired."""
    guest_name, member_name, ip_name = KINDS[kind]
    limits = rate_limits.configured(ip_name, rate_limits.client_ip(request))
    if is_fully_verified(request):
        limits += rate_limits.configured(member_name, str(request.user.pk))
    else:
        if guest_name is None:
            raise SessionRequired()
        session_key = request.session.session_key
        if not session_key or not (request.session.get(GUEST_SESSION_KEY) or request.user.is_authenticated):
            raise SessionRequired()  # keine Sitzung: kein Abruf (auch kein Upstream)
        limits += rate_limits.configured(guest_name, session_key)
    rate_limits.reserve(limits)


def limited(kind: str, *, json: bool = False):
    """Decorator: prueft das Limit VOR der eigentlichen Verarbeitung."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            try:
                check(request, kind)
            except rate_limits.RateLimited as exc:
                return too_many(request, exc.retry_after, json=json)
            except rate_limits.RateLimiterUnavailable:
                return unavailable(request, json=json)
            except SessionRequired:
                return session_required(request, json=json)
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def _finish(response, retry_after: int | None = None):
    response["Cache-Control"] = "private, no-store"
    if retry_after:
        response["Retry-After"] = str(retry_after)
    return response


def too_many(request, retry_after: int, *, json: bool = False):
    if json:
        return _finish(JsonResponse({"error": "Zu viele Anfragen", "retry_after": retry_after}, status=429), retry_after)
    return _finish(render(request, "account/rate_limited.html",
                          {"retry_minutes": max(1, retry_after // 60)}, status=429), retry_after)


def unavailable(request, *, json: bool = False):
    if json:
        return _finish(JsonResponse({"error": "Voruebergehend nicht verfuegbar"}, status=503))
    return _finish(render(request, "account/unavailable.html", status=503))


def session_required(request, *, json: bool = False):
    if json:
        return _finish(JsonResponse({"error": "Sitzung erforderlich. Bitte Seite neu laden."}, status=403))
    return _finish(render(request, "account/unavailable.html", status=403))
