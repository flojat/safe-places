"""Kontrollierte swisstopo-Anbindung (Auftrag Abschnitt 5).

- Feste Ziele: ein WMTS/XYZ-Layer und zwei REST-Endpunkte. Keine frei uebergebenen
  Hosts, Layer, Pfade, Projektionen oder URLs (kein offener Proxy, keine SSRF).
- TLS-Pruefung aktiv, keine Weiterleitungen, keine Umgebungs-Proxies, Timeouts,
  Groessen- und Content-Type-Pruefung. Keine Besucher-IP, Cookies oder Objektdaten
  werden weitergegeben; generischer User-Agent ohne Kontaktdaten.
- Jeder ausgehende Versuch wird VOR Beginn im globalen Budget reserviert (auch
  Fehler); gleichzeitige Abrufe ueber Slots mit ablaufender Lease begrenzt.
  Ist eine Pruefung nicht moeglich: kein Abruf (fail closed).
- Grundkarten-Cache nur fuer bereits angefragte Kacheln, generationsbasiert
  invalidierbar, Pfade nur aus validierten Ganzzahlen.
- Keine DB-Transaktion waehrend eines Netzwerkabrufs.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import ssl
import time
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.html import strip_tags

from .. import rate_limits
from ..geo import CoordinateError, validate_swiss_point
from ..models import ProviderSlot, TileCacheState, TileFetchLock

logger = logging.getLogger("locations.swisstopo")

TILE_HOST = "https://wmts.geo.admin.ch"
REST_HOST = "https://api3.geo.admin.ch"
_ISO_RE = re.compile(r"^[0-9T:\-+.Z ]{10,40}$")


class ProviderUnavailable(Exception):
    """Kein Providerabruf moeglich (Budget, Slot, Fehler). Nie Direkt-Fallback."""

    def __init__(self, reason: str, retry_after: int = 30):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


# --- HTTP (einzige Netzwerkfunktion; in Tests ersetzt) -----------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Weiterleitung nicht erlaubt", headers, fp)


def _build_opener():
    context = ssl.create_default_context()  # Zertifikats- und Hostnamenpruefung
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),  # keine Umgebungs-Proxies
        urllib.request.HTTPSHandler(context=context),
        _NoRedirect(),
    )


def _open(url: str, *, accept: str, timeout: float, max_bytes: int) -> tuple[int, str, bytes]:
    if not (url.startswith(TILE_HOST + "/") or url.startswith(REST_HOST + "/")):
        raise ValueError("Ziel nicht erlaubt")
    if not settings.SWISSTOPO_NETWORK_ENABLED:
        # Harte Sperre (Testsettings): kein echter Providerabruf ausserhalb freigegebener Smoke-Tests.
        raise ConnectionRefusedError("swisstopo-Netzwerkzugriff in dieser Umgebung gesperrt")
    request = urllib.request.Request(url, method="GET", headers={
        "User-Agent": settings.SWISSTOPO_USER_AGENT, "Accept": accept,
    })
    with _build_opener().open(request, timeout=timeout) as response:
        body = response.read(max_bytes + 1)
        return response.status, response.headers.get("Content-Type", ""), body


# --- Budget und gleichzeitige Abrufe -------------------------------------------------

def _reserve(kind: str) -> None:
    name = "provider_tiles_global" if kind == "tile" else "provider_rest_global"
    try:
        rate_limits.reserve(rate_limits.configured(name, "global"))
    except rate_limits.RateLimited as exc:
        raise ProviderUnavailable("budget", exc.retry_after) from exc
    except rate_limits.RateLimiterUnavailable as exc:
        raise ProviderUnavailable("limiter") from exc


def _acquire_slot(kind: str, lease_seconds: float) -> tuple[int, str]:
    n = settings.SWISSTOPO_MAX_CONCURRENT
    for slot in range(n):
        try:
            with transaction.atomic():
                ProviderSlot.objects.get_or_create(kind=kind, slot=slot)
        except IntegrityError:
            pass
    token = secrets.token_hex(8)
    now = timezone.now()
    with transaction.atomic():
        row = (
            ProviderSlot.objects.select_for_update(skip_locked=True)
            .filter(kind=kind, slot__lt=n)
            .filter(Q(expires_at__isnull=True) | Q(expires_at__lt=now))
            .order_by("slot").first()
        )
        if row is None:
            raise ProviderUnavailable("busy", 5)
        row.holder = token
        row.expires_at = now + timedelta(seconds=lease_seconds)
        row.save(update_fields=["holder", "expires_at"])
    return row.slot, token


def _release_slot(kind: str, slot: int, token: str) -> None:
    ProviderSlot.objects.filter(kind=kind, slot=slot, holder=token).update(holder="", expires_at=None)


def _fetch(kind: str, url: str, *, accept: str, timeout: float, max_bytes: int) -> tuple[int, str, bytes]:
    _reserve(kind)  # jeder Versuch zaehlt, auch wenn er fehlschlaegt
    slot, token = _acquire_slot(kind, timeout + 5)
    try:
        return _open(url, accept=accept, timeout=timeout, max_bytes=max_bytes)
    except Exception as exc:
        logger.warning("Providerabruf fehlgeschlagen: kind=%s fehler=%s", kind, type(exc).__name__)
        raise ProviderUnavailable("upstream") from exc
    finally:
        _release_slot(kind, slot, token)


# --- Kacheln -----------------------------------------------------------------------

def tile_url(z: int, x: int, y: int) -> str:
    for v in (z, x, y):
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise ValueError("ungueltige Kachelkoordinate")
    return f"{TILE_HOST}/1.0.0/{settings.SWISSTOPO_LAYER}/default/current/3857/{z}/{x}/{y}.jpeg"


def _cache_root() -> Path:
    return (Path(settings.VAR_DIR) / settings.TILE_CACHE_DIR_NAME / settings.SWISSTOPO_LAYER).resolve()


def current_generation() -> int:
    state, _ = TileCacheState.objects.get_or_create(layer=settings.SWISSTOPO_LAYER)
    return state.generation


def tile_path(generation: int, z: int, x: int, y: int) -> Path:
    for v in (generation, z, x, y):
        if not isinstance(v, int) or v < 0:
            raise ValueError("ungueltiger Cacheschluessel")
    return _cache_root() / str(generation) / str(z) / str(x) / f"{y}.jpg"


def _read_fresh(path: Path) -> bytes | None:
    try:
        age = time.time() - path.stat().st_mtime
        if age > settings.TILE_CACHE_MAX_AGE_SECONDS:
            return None
        return path.read_bytes()
    except OSError:
        return None


def _write_atomic(path: Path, data: bytes) -> None:
    from ..fsutil import make_private_dirs

    make_private_dirs(path.parent, Path(settings.VAR_DIR))
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.replace(tmp, path)


def _acquire_fetch_lock(key: str, lease_seconds: float) -> str | None:
    token = secrets.token_hex(8)
    now = timezone.now()
    try:
        with transaction.atomic():
            TileFetchLock.objects.create(key=key, holder=token, expires_at=now + timedelta(seconds=lease_seconds))
        return token
    except IntegrityError:
        taken = TileFetchLock.objects.filter(key=key, expires_at__lt=now).update(
            holder=token, expires_at=now + timedelta(seconds=lease_seconds))
        return token if taken else None


def _release_fetch_lock(key: str, token: str) -> None:
    TileFetchLock.objects.filter(key=key, holder=token).delete()


def get_tile(z: int, x: int, y: int) -> bytes:
    """Cachetreffer ohne Providerbudget; sonst genau ein Worker holt die Kachel."""
    generation = current_generation()
    path = tile_path(generation, z, x, y)
    cached = _read_fresh(path)
    if cached is not None:
        return cached
    key = f"{settings.SWISSTOPO_LAYER}:{generation}:{z}:{x}:{y}"
    token = _acquire_fetch_lock(key, settings.SWISSTOPO_TILE_TIMEOUT + 5)
    if token is None:
        deadline = time.monotonic() + settings.TILE_MISS_WAIT_SECONDS
        while time.monotonic() < deadline:  # kurz begrenzt warten, kein Upstream
            time.sleep(0.1)
            cached = _read_fresh(path)
            if cached is not None:
                return cached
        raise ProviderUnavailable("busy", 2)
    try:
        cached = _read_fresh(path)  # inzwischen von anderem Worker geschrieben?
        if cached is not None:
            return cached
        status, ctype, body = _fetch(
            "tile", tile_url(z, x, y), accept="image/jpeg",
            timeout=settings.SWISSTOPO_TILE_TIMEOUT, max_bytes=settings.SWISSTOPO_TILE_MAX_BYTES,
        )
        if (
            status != 200 or not ctype.lower().startswith("image/jpeg")
            or len(body) > settings.SWISSTOPO_TILE_MAX_BYTES or not body.startswith(b"\xff\xd8")
        ):
            raise ProviderUnavailable("invalid_response")
        _write_atomic(path, body)
        return body
    finally:
        _release_fetch_lock(key, token)


# --- Cache-Aktualitaet (REST, budgetiert) ----------------------------------------

def check_cache_update() -> bool:
    """Prueft den dokumentierten cacheUpdate-Endpunkt. Bei Aenderung neue Generation."""
    url = f"{REST_HOST}/rest/services/ech/MapServer/{settings.SWISSTOPO_LAYER}/cacheUpdate"
    status, ctype, body = _fetch("rest", url, accept="application/json",
                                 timeout=settings.SWISSTOPO_REST_TIMEOUT, max_bytes=4096)
    if status != 200 or "json" not in ctype.lower() or len(body) > 4096:
        raise ProviderUnavailable("invalid_response")
    try:
        value = str(json.loads(body.decode("utf-8")).get("cache_update", ""))
    except (ValueError, AttributeError) as exc:
        raise ProviderUnavailable("invalid_response") from exc
    if not _ISO_RE.match(value):
        raise ProviderUnavailable("invalid_response")
    with transaction.atomic():
        state, _ = TileCacheState.objects.select_for_update().get_or_create(layer=settings.SWISSTOPO_LAYER)
        changed = state.provider_cache_update != value
        if changed:
            state.generation += 1  # alte Kacheln werden nicht mehr verwendet
            state.provider_cache_update = value
        state.last_checked_at = timezone.now()
        state.save()
    return changed


def cleanup_cache() -> dict:
    """Entfernt alte Generationen, abgelaufene Kacheln und begrenzt die Gesamtgroesse."""
    import shutil

    root = _cache_root()
    generation = current_generation()
    removed = {"generations": 0, "expired": 0, "evicted": 0}
    if not root.exists():
        return removed
    for child in root.iterdir():
        if child.is_dir() and child.name != str(generation):
            shutil.rmtree(child, ignore_errors=True)
            removed["generations"] += 1
    files = []
    now = time.time()
    for path in (root / str(generation)).rglob("*.jpg") if (root / str(generation)).exists() else []:
        st = path.stat()
        if now - st.st_mtime > settings.TILE_CACHE_MAX_AGE_SECONDS:
            path.unlink(missing_ok=True)
            removed["expired"] += 1
        else:
            files.append((st.st_mtime, st.st_size, path))
    total = sum(size for _, size, _ in files)
    for _, size, path in sorted(files):
        if total <= settings.TILE_CACHE_MAX_BYTES:
            break
        path.unlink(missing_ok=True)
        total -= size
        removed["evicted"] += 1
    return removed


# --- Adresssuche (nur Arbeitsbereich, budgetiert) -------------------------------------

def geocode(text: str) -> list[dict]:
    """Fester SearchServer, nur Adressen, WGS84, wenige Treffer. Suchtext wird nicht geloggt."""
    text = " ".join((text or "").split())
    if not 3 <= len(text) <= 100 or len(text.split()) > 10:
        raise ValueError("Suchtext ungueltig")
    params = urlencode({
        "type": "locations", "searchText": text, "origins": "address",
        "sr": "4326", "limit": settings.GEOCODE_MAX_RESULTS,
    })
    url = f"{REST_HOST}/rest/services/ech/SearchServer?{params}"
    status, ctype, body = _fetch("rest", url, accept="application/json",
                                 timeout=settings.SWISSTOPO_REST_TIMEOUT,
                                 max_bytes=settings.SWISSTOPO_REST_MAX_BYTES)
    if status != 200 or "json" not in ctype.lower() or len(body) > settings.SWISSTOPO_REST_MAX_BYTES:
        raise ProviderUnavailable("invalid_response")
    try:
        results = json.loads(body.decode("utf-8")).get("results", [])
    except (ValueError, AttributeError) as exc:
        raise ProviderUnavailable("invalid_response") from exc
    out = []
    for item in results if isinstance(results, list) else []:
        attrs = item.get("attrs", {}) if isinstance(item, dict) else {}
        try:
            lat, lon = validate_swiss_point(attrs.get("lat"), attrs.get("lon"))
        except CoordinateError:
            continue
        label = strip_tags(str(attrs.get("label", "")))[:200]
        out.append({"label": label, "lat": float(lat), "lon": float(lon)})
        if len(out) >= settings.GEOCODE_MAX_RESULTS:
            break
    return out
