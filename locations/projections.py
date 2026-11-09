"""Die drei verbindlichen Projektionen (Auftrag Abschnitt 3).

Es wird nie ein vollstaendiges Modell serialisiert. Jede Ausgabe an Besucher
laeuft ueber eine dieser expliziten Feldlisten.
"""

from __future__ import annotations

from django.db.models import Prefetch, Q, QuerySet
from django.urls import reverse

from .models import Category, Photo, PublishedLocation, PublishedPhoto

# GAST: Vorschau. Keine Langbeschreibung, Kontakte, genaue Lage, interne Felder.
GUEST_FIELDS = ("public_id", "title", "category", "preview_text", "place_text", "preview_lat", "preview_lon")

# MITGLIED: Vorschau plus freigegebene Details/Kontakte/genaue Lage.
MEMBER_EXTRA_FIELDS = (
    "long_description", "details", "member_address", "member_lat", "member_lon",
    "contact_name", "contact_organisation", "contact_email", "contact_phone",
    "contact_website", "disclosure_version",
)

_CATEGORY_LABELS = dict(Category.choices)


def visible_published() -> QuerySet[PublishedLocation]:
    """Fuer Besucher sichtbar: aktiver freigegebener Stand, nicht zurueckgezogen."""
    return PublishedLocation.objects.filter(is_active=True)


def _guest_photo_prefetch() -> Prefetch:
    return Prefetch(
        "photos",
        queryset=PublishedPhoto.objects.filter(
            visibility=Photo.Visibility.GUEST_PREVIEW, photo__is_withdrawn=False,
        ).select_related("photo").order_by("sort_order", "id"),
        to_attr="guest_photos",
    )


def guest_queryset(with_photos: bool = False) -> QuerySet[PublishedLocation]:
    qs = visible_published().only(*GUEST_FIELDS)
    return qs.prefetch_related(_guest_photo_prefetch()) if with_photos else qs


_URL_VARIANTS = {"thumb": "vorschau", "detail": "detail"}


def photo_url(photo: Photo, variant: str) -> str:
    return reverse("locations:photo", args=[photo.public_id, _URL_VARIANTS[variant]])


def guest_search(qs: QuerySet, text: str) -> QuerySet:
    """Suche ausschliesslich in freigegebenen Gastfeldern."""
    if not text:
        return qs
    cond = Q(title__icontains=text) | Q(preview_text__icontains=text) | Q(place_text__icontains=text)
    matching_categories = [k for k, v in _CATEGORY_LABELS.items() if text.lower() in v.lower()]
    if matching_categories:
        cond |= Q(category__in=matching_categories)
    return qs.filter(cond)


def _guest_thumb(pub: PublishedLocation) -> dict | None:
    photos = getattr(pub, "guest_photos", None)
    if photos is None:
        photos = list(_guest_photo_prefetch().queryset.filter(published=pub)[:1])
    if not photos:
        return None
    first = photos[0]
    return {"url": photo_url(first.photo, "thumb"), "alt": first.alt_text}


def guest_projection(pub: PublishedLocation, with_thumb: bool = False) -> dict:
    data = {
        "id": pub.public_id,
        "title": pub.title,
        "category": pub.category,
        "category_label": _CATEGORY_LABELS.get(pub.category, ""),
        "preview_text": pub.preview_text,
        "place": pub.place_text,
        "lat": float(pub.preview_lat),
        "lon": float(pub.preview_lon),
    }
    if with_thumb:
        data["thumb"] = _guest_thumb(pub)
    return data


def guest_point(pub: PublishedLocation) -> dict:
    return {
        "id": pub.public_id,
        "title": pub.title,
        "category": pub.category,
        "lat": float(pub.preview_lat),
        "lon": float(pub.preview_lon),
    }


def member_projection(pub: PublishedLocation) -> dict:
    """Nur nach Pruefung von Mitgliedschaft, MFA und Freigabebuchung aufrufen (Etappe 3)."""
    data = guest_projection(pub, with_thumb=True)
    contact = {
        "name": pub.contact_name,
        "organisation": pub.contact_organisation,
        "email": pub.contact_email,
        "phone": pub.contact_phone,
        "website": pub.contact_website,
    }
    data.update({
        "long_description": pub.long_description,
        "details": pub.details,
        "exact_location": (
            {"lat": float(pub.member_lat), "lon": float(pub.member_lon), "address": pub.member_address}
            if pub.member_lat is not None else None
        ),
        "contact": {k: v for k, v in contact.items() if v},
        "photos": [
            {"thumb": photo_url(pp.photo, "thumb"), "detail": photo_url(pp.photo, "detail"), "alt": pp.alt_text}
            for pp in PublishedPhoto.objects.filter(published=pub, photo__is_withdrawn=False)
            .exclude(visibility=Photo.Visibility.INTERNAL).select_related("photo").order_by("sort_order", "id")
        ],
        "disclosure_version": pub.disclosure_version,
    })
    return data
