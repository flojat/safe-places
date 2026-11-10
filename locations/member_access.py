"""Kontrollierte Mitgliedsdetails und Freigabebuchung (Abschnitt 6).

Reihenfolge vor jeder geschuetzten Ausgabe:
1. aktives Konto, bestaetigte E-Mail, MFA-Sitzung, aktuelle Veroeffentlichung
2. gueltige Buchung fuer Objekt/disclosure_version - oder atomar innerhalb des
   Kontingents neu anlegen (nur per POST)
3. erst danach die MITGLIED-Projektion
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

from . import rate_limits
from .models import DisclosureGrant, Photo, PublishedLocation, PublishedPhoto
from .permissions import is_fully_verified


class QuotaExceeded(Exception):
    def __init__(self, retry_after: int):
        super().__init__("Kontingent erschoepft")
        self.retry_after = retry_after


class NotAllowed(Exception):
    pass


def valid_grant(request, pub: PublishedLocation) -> DisclosureGrant | None:
    """Liefert eine gueltige Buchung fuer die AKTUELLE Fassung oder None. Schreibt nichts."""
    if not is_fully_verified(request) or not pub.is_active:
        return None
    return DisclosureGrant.objects.filter(
        user=request.user, published=pub, disclosure_version=pub.disclosure_version,
        expires_at__gt=timezone.now(),
    ).first()


def open_details(request, pub: PublishedLocation) -> tuple[DisclosureGrant, bool]:
    """Bucht (falls noetig) atomar. Rueckgabe: (Buchung, neu_gebucht).

    Wiederabruf derselben gueltigen Fassung zaehlt nicht erneut. Parallele Aufrufe
    desselben Kontos werden ueber eine Sperre auf der Kontozeile serialisiert.
    """
    if not is_fully_verified(request):
        raise NotAllowed()
    User = get_user_model()
    now = timezone.now()
    limits = [
        *rate_limits.configured("grant_account", str(request.user.pk)),
        *rate_limits.configured("grant_ip", rate_limits.client_ip(request)),
        *rate_limits.configured("grant_global", "global"),
    ]
    try:
        rate_limits.prepare(limits)  # Sperrzeilen ausserhalb der Buchungstransaktion
    except DatabaseError as exc:
        raise rate_limits.RateLimiterUnavailable() from exc
    with transaction.atomic():
        User.objects.select_for_update().only("pk").get(pk=request.user.pk)
        pub = PublishedLocation.objects.select_for_update().get(pk=pub.pk)
        if not pub.is_active:
            raise NotAllowed()
        grant = DisclosureGrant.objects.filter(
            user=request.user, published=pub, disclosure_version=pub.disclosure_version,
        ).first()
        if grant is not None and grant.expires_at > now:
            return grant, False
        try:
            rate_limits.reserve(limits)
        except rate_limits.RateLimited as exc:
            raise QuotaExceeded(exc.retry_after) from exc
        expires = now + timedelta(hours=settings.DISCLOSURE_GRANT_HOURS)
        if grant is not None:  # abgelaufene Buchung derselben Fassung: neu buchen
            grant.created_at, grant.expires_at = now, expires
            grant.save(update_fields=["created_at", "expires_at"])
        else:
            try:
                with transaction.atomic():
                    grant = DisclosureGrant.objects.create(
                        user=request.user, published=pub, disclosure_version=pub.disclosure_version,
                        created_at=now, expires_at=expires,
                    )
            except IntegrityError:  # pragma: no cover - durch Kontosperre ausgeschlossen
                raise NotAllowed()
    return grant, True


def quota_status(user) -> dict:
    """Kontingenthinweis fuer das Konto (ohne Objektbezug)."""
    since = timezone.now() - timedelta(hours=24)
    used = DisclosureGrant.objects.filter(user=user, created_at__gt=since).count()
    limit = max(m for m, w in settings.RATE_LIMITS["grant_account"] if w == 86400)
    return {"used": used, "limit": limit, "remaining": max(0, limit - used)}


# --- Fotozugriff -------------------------------------------------------------

@dataclass(frozen=True)
class PhotoDecision:
    allowed: bool
    path_variant: str = ""


def can_view_private_photo(request, photo: Photo) -> bool:
    """Arbeitsfassung/Entwuerfe: vollstaendig verifizierte Verwaltung mit Rechten
    oder aktuell zugeordneter, verifizierter Objektbetreuer."""
    if not is_fully_verified(request):
        return False
    user = request.user
    from .roles import is_admin, is_editor, is_object_manager

    if is_admin(user) or is_editor(user):
        return True
    return photo.location.managed_by_id == user.pk and is_object_manager(user)


def photo_access(request, photo: Photo, variant: str) -> bool:
    """Entscheidet ueber die Auslieferung einer Fotovariante. Bei jedem Abruf neu."""
    if can_view_private_photo(request, photo):
        return True
    if photo.is_withdrawn:
        return False
    published = (
        PublishedPhoto.objects.select_related("published")
        .filter(photo=photo, published__is_active=True).first()
    )
    if published is None:
        return False
    if published.visibility == Photo.Visibility.GUEST_PREVIEW:
        return True  # Gastbilder: Vorschau und Galeriegroesse fuer alle Besucher
    if published.visibility in (Photo.Visibility.GUEST_PREVIEW, Photo.Visibility.MEMBER_DETAIL):
        return valid_grant(request, published.published) is not None
    return False


def cleanup_expired_grants(older_than_hours: int = 48) -> int:
    cutoff = timezone.now() - timedelta(hours=older_than_hours)
    deleted, _ = DisclosureGrant.objects.filter(expires_at__lt=cutoff).delete()
    return deleted
