"""Koordinatenpruefung, grobe Vorschaulage und bbox-Parsing (WGS84, Grad).

Leaflet verwendet [lat, lon]; JSON-Antworten nutzen benannte Felder lat/lon.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

# Plausibilitaetsrechteck Schweiz (keine exakte Landesgrenze).
CH_LAT_MIN, CH_LAT_MAX = Decimal("45.80"), Decimal("47.85")
CH_LON_MIN, CH_LON_MAX = Decimal("5.90"), Decimal("10.55")
# Darstellungsrand fuer Karte/Kacheln.
MAP_MARGIN = Decimal("0.30")

# Rastergroesse fuer die grobe, stabile Gastlage (~2 km).
PREVIEW_GRID_LAT = Decimal("0.02")
PREVIEW_GRID_LON = Decimal("0.03")
_Q6 = Decimal("0.000001")


class CoordinateError(ValueError):
    pass


def to_coordinate(value, kind: str) -> Decimal:
    """Wandelt in endliche Decimal-Grad um; NaN/Infinity/Unsinn wird abgewiesen."""
    if isinstance(value, bool) or value is None or value == "":
        raise CoordinateError(f"{kind} fehlt")
    try:
        if isinstance(value, float) and not math.isfinite(value):
            raise CoordinateError(f"{kind} ist keine endliche Zahl")
        dec = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise CoordinateError(f"{kind} ist keine Zahl") from exc
    if not dec.is_finite():
        raise CoordinateError(f"{kind} ist keine endliche Zahl")
    limit = 90 if kind == "lat" else 180
    if not (-limit <= dec <= limit):
        raise CoordinateError(f"{kind} ausserhalb des gueltigen Bereichs")
    return dec.quantize(_Q6, rounding=ROUND_HALF_UP)


def validate_swiss_point(lat, lon) -> tuple[Decimal, Decimal]:
    la, lo = to_coordinate(lat, "lat"), to_coordinate(lon, "lon")
    if not (CH_LAT_MIN <= la <= CH_LAT_MAX and CH_LON_MIN <= lo <= CH_LON_MAX):
        raise CoordinateError("Punkt liegt ausserhalb des Schweizer Plausibilitaetsrechtecks")
    return la, lo


def coarse_preview_point(lat, lon) -> tuple[Decimal, Decimal]:
    """Deterministisch auf Rasterzellenmitte: stabil, keine Zufallsverschiebung."""
    la, lo = to_coordinate(lat, "lat"), to_coordinate(lon, "lon")

    def snap(v: Decimal, step: Decimal) -> Decimal:
        cell = (v / step).to_integral_value(rounding=ROUND_FLOOR)
        return (cell * step + step / 2).quantize(_Q6)

    return snap(la, PREVIEW_GRID_LAT), snap(lo, PREVIEW_GRID_LON)


@dataclass(frozen=True)
class BBox:
    west: Decimal
    south: Decimal
    east: Decimal
    north: Decimal


def parse_bbox(raw: str | None) -> BBox | None:
    """bbox=west,south,east,north. Vier endliche Zahlen, korrekte Reihenfolge,
    auf Schweiz plus Rand beschraenkt. None bei fehlendem Parameter."""
    if raw is None or raw == "":
        return None
    if len(raw) > 100:
        raise CoordinateError("bbox zu lang")
    parts = raw.split(",")
    if len(parts) != 4:
        raise CoordinateError("bbox braucht vier Werte")
    west, east = to_coordinate(parts[0], "lon"), to_coordinate(parts[2], "lon")
    south, north = to_coordinate(parts[1], "lat"), to_coordinate(parts[3], "lat")
    if west >= east or south >= north:
        raise CoordinateError("bbox-Reihenfolge ungueltig")
    # Auf erlaubten Bereich zuschneiden; vollstaendig ausserhalb -> Fehler.
    w, e = max(west, CH_LON_MIN - MAP_MARGIN), min(east, CH_LON_MAX + MAP_MARGIN)
    s, n = max(south, CH_LAT_MIN - MAP_MARGIN), min(north, CH_LAT_MAX + MAP_MARGIN)
    if w >= e or s >= n:
        raise CoordinateError("bbox ausserhalb des Kartenbereichs")
    return BBox(w, s, e, n)


# --- Web-Mercator-Kacheln (EPSG:3857, XYZ) ---------------------------------

def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """(west, south, east, north) in Grad fuer eine XYZ-Kachel."""
    n = 2 ** z

    def lat(yy: int) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))

    return x / n * 360 - 180, lat(y + 1), (x + 1) / n * 360 - 180, lat(y)


def tile_intersects_switzerland(z: int, x: int, y: int) -> bool:
    w, s, e, n = tile_bounds(z, x, y)
    m = float(MAP_MARGIN)
    return not (
        e < float(CH_LON_MIN) - m or w > float(CH_LON_MAX) + m
        or n < float(CH_LAT_MIN) - m or s > float(CH_LAT_MAX) + m
    )
