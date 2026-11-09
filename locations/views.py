"""Besucheransichten: Karte/Liste, Vorschau/Mitgliedsdetails, Daten-Endpunkte,
Fotos und Kachelweg.

- Gast-/Listen-/Kartendaten immer aus der GAST-Projektion.
- Mitgliedsdetails erst nach Pruefung von Zustand, Veroeffentlichung und Buchung.
- Alle Besucherwege mit Request-Limits (request_limits.py) vor der Verarbeitung.
"""

from __future__ import annotations

import io
import math
from functools import lru_cache

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_GET, require_POST, require_safe

from . import member_access, projections, rate_limits, request_limits
from .geo import CoordinateError, parse_bbox, tile_intersects_switzerland
from .models import Category, Photo
from .permissions import fully_verified_required, is_fully_verified
from .services import images, swisstopo

MAX_POINTS = 500
GUEST_PAGE_SIZE = 10
MEMBER_PAGE_SIZE = 50
MAX_QUERY_LENGTH = 80
MAX_PAGE = 1000


def _no_store(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "private, no-store"
    patch_vary_headers(response, ["Cookie"])
    return response


def _bad_request(message: str) -> JsonResponse:
    return _no_store(JsonResponse({"error": message}, status=400))


class _FilterError(ValueError):
    pass


def _filtered_queryset(request, with_photos: bool = False):
    """Gemeinsamer Ausschnitts-/Suchfilter fuer Punkte und Liste."""
    qs = projections.guest_queryset(with_photos=with_photos)
    try:
        bbox = parse_bbox(request.GET.get("bbox"))
    except CoordinateError as exc:
        raise _FilterError(str(exc)) from exc
    if bbox is not None:
        qs = qs.filter(
            preview_lat__gte=bbox.south, preview_lat__lte=bbox.north,
            preview_lon__gte=bbox.west, preview_lon__lte=bbox.east,
        )
    q = request.GET.get("q", "").strip()
    if len(q) > MAX_QUERY_LENGTH:
        raise _FilterError("Suchtext zu lang")
    qs = projections.guest_search(qs, q)
    category = request.GET.get("category", "")
    if category:
        if category not in Category.values:
            raise _FilterError("Unbekannte Kategorie")
        qs = qs.filter(category=category)
    return qs


@require_safe
def home(request):
    request_limits.ensure_guest_session(request)
    response = render(request, "locations/home.html", {
        "categories": Category.choices,
        "map_config": {
            "minZoom": settings.MAP_MIN_ZOOM,
            "maxZoom": settings.MAP_MAX_ZOOM,
            "tileSource": settings.MAP_TILE_SOURCE,
        },
    })
    return _no_store(response)


@require_GET
@request_limits.limited("query", json=True)
def api_points(request):
    try:
        qs = _filtered_queryset(request)
    except _FilterError as exc:
        return _bad_request(str(exc))
    count = qs.count()
    too_many = count > MAX_POINTS
    points = [] if too_many else [projections.guest_point(p) for p in qs.order_by("id")[:MAX_POINTS]]
    return _no_store(JsonResponse({
        "count": count,
        "too_many": too_many,
        "max_points": MAX_POINTS,
        "points": points,
    }))


@require_GET
@request_limits.limited("query", json=True)
def api_list(request):
    try:
        qs = _filtered_queryset(request, with_photos=True)
        page = int(request.GET.get("page", "1"))
    except _FilterError as exc:
        return _bad_request(str(exc))
    except ValueError:
        return _bad_request("Seite ungueltig")
    if not 1 <= page <= MAX_PAGE:
        return _bad_request("Seite ungueltig")
    page_size = MEMBER_PAGE_SIZE if is_fully_verified(request) else GUEST_PAGE_SIZE
    count = qs.count()
    start = (page - 1) * page_size
    results = [
        projections.guest_projection(p, with_thumb=True)
        for p in qs.order_by("title", "id")[start:start + page_size]
    ]
    return _no_store(JsonResponse({
        "count": count,
        "page": page,
        "page_size": page_size,
        "pages": max(1, math.ceil(count / page_size)),
        "results": results,
    }))


# --- Vorschau / Mitgliedsdetails ---------------------------------------------

@require_safe
def preview(request, public_id: str):
    """Huellseite fuer direkte Links. Unveroeffentlicht/nicht vorhanden: einheitlich 404.

    GET bucht nie. Mitglieder mit gueltiger Buchung der aktuellen Fassung sehen
    die Details; sonst die Vorschau mit bewusster Oeffnungsaktion (POST).
    """
    request_limits.ensure_guest_session(request)
    member = is_fully_verified(request)
    pub = projections.visible_published().filter(public_id=public_id).first()
    grant = member_access.valid_grant(request, pub) if (member and pub is not None) else None
    try:
        request_limits.check(request, "detail" if grant else "query")
    except rate_limits.RateLimited as exc:
        return request_limits.too_many(request, exc.retry_after)
    except rate_limits.RateLimiterUnavailable:
        return request_limits.unavailable(request)
    except request_limits.SessionRequired:
        return request_limits.session_required(request)
    if pub is None:
        raise Http404()

    if grant is not None:
        context = {"obj": projections.member_projection(pub), "grant": grant}
        return _no_store(render(request, "locations/member_detail.html", context))

    context = {"obj": projections.guest_projection(pub, with_thumb=True), "member": member}
    if member:
        context["quota"] = member_access.quota_status(request.user)
    return _no_store(render(request, "locations/preview.html", context))


@require_POST
@fully_verified_required
def open_details(request, public_id: str):
    """Bewusstes Oeffnen: bucht innerhalb des Kontingents VOR jeder Datenausgabe."""
    pub = projections.visible_published().filter(public_id=public_id).first()
    if pub is None:
        raise Http404()
    try:
        member_access.open_details(request, pub)
    except member_access.QuotaExceeded as exc:
        available_at = timezone.localtime(timezone.now() + timezone.timedelta(seconds=exc.retry_after))
        response = render(request, "locations/quota_exceeded.html", {
            "obj": projections.guest_projection(pub),
            "available_at": available_at,
        }, status=429)
        response["Retry-After"] = str(exc.retry_after)
        return _no_store(response)
    except rate_limits.RateLimiterUnavailable:
        return request_limits.unavailable(request)
    except member_access.NotAllowed:
        raise Http404()
    return redirect("locations:preview", public_id=public_id)


# --- Fotos ---------------------------------------------------------------------

_VARIANT_NAMES = {"vorschau": "thumb", "detail": "detail"}


@require_safe
@request_limits.limited("photo")
def photo(request, public_id: str, variant: str):
    """Kontrollierte Fotoauslieferung; keine freien Pfade, Pruefung bei jedem Abruf."""
    internal_variant = _VARIANT_NAMES.get(variant)
    if internal_variant is None:
        raise Http404()
    photo_obj = Photo.objects.select_related("location").filter(public_id=public_id).first()
    if photo_obj is None or not member_access.photo_access(request, photo_obj, internal_variant):
        raise Http404()
    try:
        path = images.variant_path(photo_obj.storage_key, internal_variant)
        handle = open(path, "rb")
    except (ValueError, OSError):
        raise Http404()
    response = FileResponse(handle, content_type="image/jpeg")
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = "inline"
    return _no_store(response)


# --- Kachelweg -------------------------------------------------------------

@require_safe
@request_limits.limited("tile")
def tile(request, z: int, x: int, y: int):
    """Gleichursprunglicher Kachelweg /map/tiles/{z}/{x}/{y}.jpeg.

    MAP_TILE_SOURCE=local_test: lokal erzeugte Testkacheln (Entwicklung/Tests).
    MAP_TILE_SOURCE=swisstopo: budgetiertes Gateway mit Cache (services/swisstopo.py).
    """
    if not settings.MAP_MIN_ZOOM <= z <= settings.MAP_MAX_ZOOM:
        raise Http404()
    n = 2 ** z
    if not (0 <= x < n and 0 <= y < n) or not tile_intersects_switzerland(z, x, y):
        raise Http404()
    if settings.MAP_TILE_SOURCE == "swisstopo":
        try:
            data = swisstopo.get_tile(z, x, y)
        except swisstopo.ProviderUnavailable as exc:
            # Kein Direkt-Fallback; Liste bleibt bedienbar.
            response = HttpResponse(status=503)
            response["Retry-After"] = str(exc.retry_after)
            response["Cache-Control"] = "no-store"
            return response
    elif settings.MAP_TILE_SOURCE == "local_test":
        data = _render_test_tile(z, x, y)
    else:
        raise Http404()
    response = HttpResponse(data, content_type="image/jpeg")
    response["Cache-Control"] = "private, max-age=86400"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@lru_cache(maxsize=512)
def _render_test_tile(z: int, x: int, y: int) -> bytes:
    from PIL import Image, ImageDraw

    shade = 236 if (x + y) % 2 == 0 else 228
    img = Image.new("RGB", (256, 256), (shade, shade + 4, shade))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 255, 255], outline=(190, 196, 190))
    draw.text((8, 8), f"Testkachel {z}/{x}/{y}", fill=(120, 126, 120))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return buf.getvalue()
