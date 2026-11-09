"""Gastansichten: Karte/Liste, Vorschau, Daten-Endpunkte und Kachelweg.

Alle Besucherausgaben verwenden die GAST-Projektion (projections.py).
Request-Limits fuer Gaeste folgen in Etappe 3 (siehe IMPLEMENTIERUNGSSTATUS.md).
"""

from __future__ import annotations

import io
import math
from functools import lru_cache

from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_GET, require_safe

from . import projections
from .geo import CoordinateError, parse_bbox, tile_intersects_switzerland
from .models import Category

MAX_POINTS = 500
GUEST_PAGE_SIZE = 10
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


def _filtered_queryset(request):
    """Gemeinsamer Ausschnitts-/Suchfilter fuer Punkte und Liste."""
    qs = projections.guest_queryset()
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


@require_GET
def home(request):
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
def api_list(request):
    try:
        qs = _filtered_queryset(request)
        page = int(request.GET.get("page", "1"))
    except _FilterError as exc:
        return _bad_request(str(exc))
    except ValueError:
        return _bad_request("Seite ungueltig")
    if not 1 <= page <= MAX_PAGE:
        return _bad_request("Seite ungueltig")
    page_size = GUEST_PAGE_SIZE  # Mitgliedsseiten bis 50 ab Etappe 2/3
    count = qs.count()
    start = (page - 1) * page_size
    results = [projections.guest_projection(p) for p in qs.order_by("title", "id")[start:start + page_size]]
    return _no_store(JsonResponse({
        "count": count,
        "page": page,
        "page_size": page_size,
        "pages": max(1, math.ceil(count / page_size)),
        "results": results,
    }))


@require_GET
def preview(request, public_id: str):
    """Vorschau-/Huellseite. Unveroeffentlicht und nicht vorhanden: einheitlich 404."""
    pub = projections.guest_queryset().filter(public_id=public_id).first()
    if pub is None:
        raise Http404()
    return _no_store(render(request, "locations/preview.html", {
        "obj": projections.guest_projection(pub),
    }))


# --- Kachelweg -------------------------------------------------------------

@require_safe
def tile(request, z: int, x: int, y: int):
    """Gleichursprunglicher Kachelweg /map/tiles/{z}/{x}/{y}.jpeg.

    Etappe 1: ausschliesslich lokal erzeugte Testkacheln, kein Providerabruf.
    Etappe 5 ersetzt dies durch das budgetierte swisstopo-Gateway.
    """
    if not settings.MAP_MIN_ZOOM <= z <= settings.MAP_MAX_ZOOM:
        raise Http404()
    n = 2 ** z
    if not (0 <= x < n and 0 <= y < n) or not tile_intersects_switzerland(z, x, y):
        raise Http404()
    if settings.MAP_TILE_SOURCE != "local_test":
        raise Http404()  # swisstopo-Gateway noch nicht implementiert; kein Direkt-Fallback
    response = HttpResponse(_render_test_tile(z, x, y), content_type="image/jpeg")
    response["Cache-Control"] = "private, max-age=3600"
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
