"""Die drei verbindlichen Projektionen (Auftrag Abschnitt 3).

Es wird nie ein vollstaendiges Modell serialisiert. Jede Ausgabe an Besucher
laeuft ueber eine dieser expliziten Feldlisten.
"""

from __future__ import annotations

from django.db.models import Prefetch, Q, QuerySet
from django.urls import reverse

from . import categories as category_store
from .models import Photo, PublishedLocation, PublishedPhoto

# GAST: Vorschau. Keine Langbeschreibung, Kontakte, genaue Lage, interne Felder.
GUEST_FIELDS = ("public_id", "title", "categories", "preview_text", "place_text", "preview_lat", "preview_lon",
                "guest_long_description_released", "guest_details_released", "guest_contacts_released")

# Einzeln fuer Besucher freigebbar (Schalter im freigegebenen Stand, Standard aus).
GUEST_RELEASABLE = {
    "long_description": "guest_long_description_released",
    "details": "guest_details_released",
    "contact": "guest_contacts_released",
}

# MITGLIED: Vorschau plus freigegebene Details/Kontakte/genaue Lage.
MEMBER_EXTRA_FIELDS = (
    "long_description", "details", "member_address", "member_lat", "member_lon",
    "contact_name", "contact_organisation", "contact_email", "contact_phone",
    "contact_website", "disclosure_version",
)

def category_items(values, table=None) -> list[dict]:
    """Aktive Kategorien als Wert, Bezeichnung und Symbol (gepflegte Reihenfolge)."""
    return category_store.items(values, table)


def category_filter(values) -> Q:
    """Mindestens eine der gewaehlten Kategorien passt (ODER)."""
    cond = Q()
    for value in values:
        cond |= Q(categories__contains=[value])
    return cond


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
    for value, cat in category_store.lookup().items():
        if text.lower() in cat.label.lower():
            cond |= Q(categories__contains=[value])
    return qs.filter(cond)


def _guest_thumb(pub: PublishedLocation) -> dict | None:
    photos = getattr(pub, "guest_photos", None)
    if photos is None:
        photos = list(_guest_photo_prefetch().queryset.filter(published=pub)[:1])
    if not photos:
        return None
    first = photos[0]
    return {"url": photo_url(first.photo, "thumb"), "alt": first.alt_text}


def _contact(pub: PublishedLocation) -> dict:
    contact = {
        "name": pub.contact_name,
        "organisation": pub.contact_organisation,
        "email": pub.contact_email,
        "phone": pub.contact_phone,
        "website": pub.contact_website,
    }
    return {k: v for k, v in contact.items() if v}


def guest_released(pub: PublishedLocation) -> dict:
    """Nur die einzeln fuer Besucher freigegebenen Mitgliedsfelder (leere werden weggelassen)."""
    out = {}
    if pub.guest_long_description_released and pub.long_description:
        out["long_description"] = pub.long_description
    if pub.guest_details_released and pub.details:
        out["details"] = pub.details
    if pub.guest_contacts_released:
        contact = _contact(pub)
        if contact:
            out["contact"] = contact
    return out


def gallery_item(pp: PublishedPhoto) -> dict:
    """Ein Galeriebild: Vorschau, Galeriegroesse und Bildbeschreibung (zugleich Alternativtext)."""
    return {"thumb": photo_url(pp.photo, "thumb"), "full": photo_url(pp.photo, "detail"),
            "description": pp.alt_text}


def guest_gallery(pub: PublishedLocation) -> list[dict]:
    """Nur fuer Gaeste freigegebene Bilder (Gastvorschau), nicht zurueckgezogen, in Reihenfolge."""
    photos = getattr(pub, "guest_photos", None)
    if photos is None:
        photos = list(_guest_photo_prefetch().queryset.filter(published=pub))
    return [gallery_item(pp) for pp in photos]


def guest_projection(pub: PublishedLocation, with_thumb: bool = False, with_released: bool = False,
                     category_table=None) -> dict:
    data = {
        "id": pub.public_id,
        "title": pub.title,
        "categories": category_items(pub.categories, category_table),
        "preview_text": pub.preview_text,
        "place": pub.place_text,
        "lat": float(pub.preview_lat),
        "lon": float(pub.preview_lon),
    }
    if with_thumb:
        data["thumb"] = _guest_thumb(pub)
        data["gallery"] = guest_gallery(pub)
    if with_released:
        data.update(guest_released(pub))
    return data


def guest_point(pub: PublishedLocation, category_table=None) -> dict:
    table = category_store.lookup() if category_table is None else category_table
    return {
        "id": pub.public_id,
        "title": pub.title,
        "categories": [c for c in pub.categories if c in table],
        "lat": float(pub.preview_lat),
        "lon": float(pub.preview_lon),
    }


def member_projection(pub: PublishedLocation) -> dict:
    """Nur nach Pruefung von Mitgliedschaft, MFA und Freigabebuchung aufrufen (Etappe 3)."""
    data = guest_projection(pub, with_thumb=True)
    data.update({
        "long_description": pub.long_description,
        "details": pub.details,
        "exact_location": (
            {"lat": float(pub.member_lat), "lon": float(pub.member_lon), "address": pub.member_address}
            if pub.member_lat is not None else None
        ),
        "contact": _contact(pub),
        # Mitglied mit gueltiger Freigabebuchung: Gast- und Mitgliederbilder (nie interne).
        "gallery": [
            gallery_item(pp)
            for pp in PublishedPhoto.objects.filter(published=pub, photo__is_withdrawn=False)
            .exclude(visibility=Photo.Visibility.INTERNAL).select_related("photo").order_by("sort_order", "id")
        ],
        "disclosure_version": pub.disclosure_version,
    })
    return data
