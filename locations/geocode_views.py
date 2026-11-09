"""Adresssuche nur im Arbeitsbereich per ausdruecklichem Knopfdruck (Abschnitt 5/7).

Betreuung: nur beim Bearbeiten eines eigenen, aktuell zugeordneten Objekts.
Redaktion/Administration: im OTP-geschuetzten Admin. Normale Mitglieder: kein Zugang.
Suchtexte werden weder protokolliert noch gespeichert.
"""

from __future__ import annotations

from django.http import Http404, JsonResponse
from django.views.decorators.http import require_POST

from . import rate_limits
from .models import Location
from .permissions import can_manage_location, can_publish
from .services import swisstopo


def _respond(request):
    try:
        rate_limits.reserve(rate_limits.configured("geocode_account", str(request.user.pk)))
        results = swisstopo.geocode(request.POST.get("q", ""))
    except ValueError:
        return JsonResponse({"error": "Bitte 3 bis 100 Zeichen eingeben."}, status=400)
    except rate_limits.RateLimited as exc:
        return JsonResponse({"error": "Zu viele Suchanfragen. Bitte kurz warten."}, status=429,
                            headers={"Retry-After": str(exc.retry_after)})
    except (rate_limits.RateLimiterUnavailable, swisstopo.ProviderUnavailable):
        return JsonResponse({"error": "Adresssuche momentan nicht verfuegbar. Lage bitte manuell eingeben."},
                            status=503)
    response = JsonResponse({"results": results})
    response["Cache-Control"] = "private, no-store"
    return response


@require_POST
def portal_geocode(request, public_id: str):
    loc = Location.objects.filter(public_id=public_id).first()
    if loc is None or not can_manage_location(request, loc):
        raise Http404()
    return _respond(request)


@require_POST
def admin_geocode(request):
    if not can_publish(request):
        raise Http404()
    return _respond(request)
