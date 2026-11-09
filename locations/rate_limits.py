"""Atomare, persistente Limits in MariaDB (rollende Zeitfenster).

Prinzip: Je (Bereich, Schluessel) eine Sperrzeile (RateBucket). Eine Reservierung
sperrt alle betroffenen Zeilen in fester Reihenfolge (SELECT ... FOR UPDATE),
zaehlt die Ereignisse im Fenster und legt nur dann neue Ereignisse an, wenn
ALLE Grenzen eingehalten sind. Keine Prozesszaehler, kein Cache.

Sperrzeilen werden vorab ausserhalb der Reservierung angelegt, damit parallele
INSERTs auf denselben Schluessel keine Deadlocks in der Reservierung erzeugen.
Deadlocks/Lock-Timeouts werden begrenzt wiederholt (nur ausserhalb einer
umgebenden Transaktion), danach fail closed.

Schluessel werden mit einem HMAC pseudonymisiert (keine Klartext-IP oder -Adresse).
Faellt die Datenbank aus, wird RateLimiterUnavailable ausgeloest; Aufrufer muessen
dann sicherheitskritische Aktionen ablehnen (fail closed).
"""

from __future__ import annotations

import ipaddress
import math
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, IntegrityError, OperationalError, connection, transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import RateBucket, RateEvent

_DEADLOCK_CODES = {1205, 1213}  # Lock wait timeout, Deadlock
_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Limit:
    scope: str
    raw_key: str
    max_events: int
    window_seconds: int


class RateLimited(Exception):
    def __init__(self, retry_after: int, scope: str):
        super().__init__(f"Limit erreicht ({scope})")
        self.retry_after = max(1, int(retry_after))
        self.scope = scope


class RateLimiterUnavailable(Exception):
    pass


def pseudonymize(scope: str, raw_key: str) -> str:
    return salted_hmac("adresskarte.rate-limit", f"{scope}\x00{raw_key}", algorithm="sha256").hexdigest()[:64]


def client_ip(request) -> str:
    """Nur die echte Verbindungsadresse. Forwarded-Header werden ignoriert,
    bis eine bestaetigte Proxykette konfiguriert ist (Deployment)."""
    raw = request.META.get("REMOTE_ADDR", "") or "unknown"
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return "unknown"
    if ip.version == 6:
        # IPv6-Endkunden erhalten typischerweise ein /64: als eine Quelle zaehlen.
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def _ensure_bucket(scope: str, key: str) -> None:
    if RateBucket.objects.filter(scope=scope, key=key).exists():
        return
    try:
        with transaction.atomic():
            RateBucket.objects.create(scope=scope, key=key)
    except IntegrityError:
        pass  # parallel angelegt


def _reserve_locked(keyed: list[tuple[Limit, str]], bucket_keys: list[tuple[str, str]]) -> None:
    now = timezone.now()
    with transaction.atomic():
        buckets: dict[tuple[str, str], RateBucket] = {}
        for scope, key in bucket_keys:  # feste Sperrreihenfolge
            buckets[(scope, key)] = RateBucket.objects.select_for_update().get(scope=scope, key=key)
        denied: list[RateLimited] = []
        for lim, key in keyed:
            bucket = buckets[(lim.scope, key)]
            since = now - timedelta(seconds=lim.window_seconds)
            events = RateEvent.objects.filter(bucket=bucket, created_at__gt=since)
            if events.count() >= lim.max_events:
                oldest = events.order_by("created_at").values_list("created_at", flat=True).first()
                wait = lim.window_seconds - (now - oldest).total_seconds() if oldest else lim.window_seconds
                denied.append(RateLimited(math.ceil(wait), lim.scope))
        if denied:
            raise max(denied, key=lambda d: d.retry_after)
        RateEvent.objects.bulk_create([RateEvent(bucket=b, created_at=now) for b in buckets.values()])


def reserve(limits: list[Limit]) -> None:
    """Reserviert atomar ein Ereignis in allen betroffenen Buckets oder keines."""
    if not limits:
        return
    keyed = [(lim, pseudonymize(lim.scope, lim.raw_key)) for lim in limits]
    bucket_keys = sorted({(lim.scope, key) for lim, key in keyed})
    for attempt in range(_MAX_ATTEMPTS):
        try:
            for scope, key in bucket_keys:
                _ensure_bucket(scope, key)
            _reserve_locked(keyed, bucket_keys)
            return
        except RateLimited:
            raise
        except OperationalError as exc:
            code = exc.args[0] if exc.args else None
            retry_possible = not connection.in_atomic_block and attempt + 1 < _MAX_ATTEMPTS
            if code in _DEADLOCK_CODES and retry_possible:
                continue
            raise RateLimiterUnavailable("Limiter nicht verfuegbar") from exc
        except DatabaseError as exc:
            raise RateLimiterUnavailable("Limiter nicht verfuegbar") from exc


def configured(name: str, raw_key: str) -> list[Limit]:
    """Limits aus settings.RATE_LIMITS: {name: [(max, window_seconds), ...]}."""
    return [Limit(name, raw_key, m, w) for m, w in settings.RATE_LIMITS[name]]


def cleanup(older_than_hours: int = 48) -> int:
    cutoff = timezone.now() - timedelta(hours=older_than_hours)
    deleted, _ = RateEvent.objects.filter(created_at__lt=cutoff).delete()
    RateBucket.objects.filter(created_at__lt=cutoff, events__isnull=True).delete()
    return deleted
