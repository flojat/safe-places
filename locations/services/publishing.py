"""Einreichen, Freigeben und Zurueckziehen mit kurzen Transaktionen und Versionsvergleich.

Berechtigungspruefung erfolgt im aufrufenden View/Admin ueber die Policies
(Etappe 4); dieser Service sichert Konsistenz und Atomaritaet.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from ..geo import CoordinateError, coarse_preview_point, validate_swiss_point
from ..models import AuditEvent, Location, Photo, PublishedLocation, PublishedPhoto


class PublishError(Exception):
    pass


class VersionConflict(PublishError):
    pass


def missing_for_publication(loc: Location) -> list[str]:
    missing = []
    for field, label in (
        ("title", "Titel"), ("category", "Kategorie"), ("preview_text", "Vorschautext"),
        ("place_text", "Ort/Region"), ("long_description", "Langbeschreibung"),
    ):
        if not getattr(loc, field).strip():
            missing.append(label)
    try:
        if loc.preview_lat is not None:
            validate_swiss_point(loc.preview_lat, loc.preview_lon)
        elif loc.exact_lat is not None:
            validate_swiss_point(loc.exact_lat, loc.exact_lon)
        else:
            missing.append("Lage")
    except CoordinateError:
        missing.append("gueltige Lage in der Schweiz")
    if loc.member_exact_location_released and loc.exact_lat is None:
        missing.append("genaue Lage (als freigegeben markiert)")
    if loc.contacts_released and not any(
        (loc.contact_name, loc.contact_organisation, loc.contact_email, loc.contact_phone, loc.contact_website)
    ):
        missing.append("Kontaktangaben (als freigegeben markiert)")
    return missing


def _member_signature(values: dict, photo_ids: list) -> tuple:
    keys = ("long_description", "details", "member_address", "member_lat", "member_lon",
            "contact_name", "contact_organisation", "contact_email", "contact_phone", "contact_website")
    return tuple(str(values.get(k) or "") for k in keys) + (tuple(photo_ids),)


CONTACT_FIELDS = ("contact_name", "contact_organisation", "contact_email", "contact_phone", "contact_website")


def snapshot_values(loc: Location) -> tuple[dict, list]:
    """Werte, die eine Freigabe in den veroeffentlichten Stand uebernehmen wuerde.

    Wird auch fuer die Vorschau der Arbeitsfassung genutzt (ohne Speichern).
    Interne Felder werden nie aufgenommen.
    """
    if loc.preview_lat is not None:
        p_lat, p_lon = validate_swiss_point(loc.preview_lat, loc.preview_lon)
    else:
        p_lat, p_lon = coarse_preview_point(loc.exact_lat, loc.exact_lon)
    member_lat = member_lon = None
    member_address = ""
    if loc.member_exact_location_released:
        member_lat, member_lon = validate_swiss_point(loc.exact_lat, loc.exact_lon)
        member_address = loc.exact_address
    contact = (
        {f: getattr(loc, f) for f in CONTACT_FIELDS} if loc.contacts_released
        else dict.fromkeys(CONTACT_FIELDS, "")
    )
    values = {
        "title": loc.title, "category": loc.category, "preview_text": loc.preview_text,
        "place_text": loc.place_text, "preview_lat": p_lat, "preview_lon": p_lon,
        "long_description": loc.long_description, "details": loc.details,
        "member_address": member_address, "member_lat": member_lat, "member_lon": member_lon,
        **contact, "is_demo": loc.is_demo,
    }
    photos = list(
        loc.photos.filter(is_withdrawn=False)
        .exclude(visibility=Photo.Visibility.INTERNAL)
        .order_by("sort_order", "id")
    )
    return values, photos


def submit(location_id: int, actor, expected_work_version: int) -> Location:
    with transaction.atomic():
        loc = Location.objects.select_for_update().get(pk=location_id)
        if loc.work_version != expected_work_version:
            raise VersionConflict("Arbeitsfassung wurde inzwischen geaendert")
        if loc.review_state == Location.ReviewState.SUBMITTED:
            return loc
        loc.review_state = Location.ReviewState.SUBMITTED
        loc.submitted_at = timezone.now()
        loc.save(update_fields=["review_state", "submitted_at", "updated_at"])
        AuditEvent.objects.create(actor=actor, action="submit", target_type="location",
                                  target_id=loc.public_id, detail=f"v{loc.work_version}")
    return loc


def publish(location_id: int, reviewer, expected_work_version: int) -> PublishedLocation:
    """Uebernimmt die eingereichte Arbeitsfassung atomar in den freigegebenen Stand."""
    with transaction.atomic():
        loc = Location.objects.select_for_update().get(pk=location_id)
        if loc.work_version != expected_work_version:
            raise VersionConflict("Arbeitsfassung wurde seit der Pruefung geaendert")
        if loc.review_state != Location.ReviewState.SUBMITTED:
            raise PublishError("Nur eingereichte Fassungen koennen freigegeben werden")
        missing = missing_for_publication(loc)
        if missing:
            raise PublishError("Unvollstaendig: " + ", ".join(missing))

        values, photos = snapshot_values(loc)
        member_photo_ids = [p.id for p in photos if p.visibility == Photo.Visibility.MEMBER_DETAIL]

        now = timezone.now()
        pub = PublishedLocation.objects.select_for_update().filter(location=loc).first()
        if pub is None:
            pub = PublishedLocation(location=loc, public_id=loc.public_id, disclosure_version=1)
        else:
            old_values = {k: getattr(pub, k) for k in values}
            old_member_photos = list(
                pub.photos.filter(visibility=Photo.Visibility.MEMBER_DETAIL)
                .order_by("sort_order", "id").values_list("photo_id", flat=True)
            )
            if _member_signature(old_values, old_member_photos) != _member_signature(values, member_photo_ids) \
                    or not pub.is_active:
                pub.disclosure_version += 1
        for k, v in values.items():
            setattr(pub, k, v)
        pub.is_active = True
        pub.withdrawn_at = None
        pub.source_work_version = loc.work_version
        pub.published_at = now
        pub.published_by = reviewer
        pub.save()

        pub.photos.all().delete()
        PublishedPhoto.objects.bulk_create([
            PublishedPhoto(published=pub, photo=p, visibility=p.visibility,
                           alt_text=p.alt_text, sort_order=p.sort_order)
            for p in photos
        ])

        loc.review_state = Location.ReviewState.APPROVED
        loc.reviewed_at = now
        loc.save(update_fields=["review_state", "reviewed_at", "updated_at"])
        AuditEvent.objects.create(actor=reviewer, action="publish", target_type="location",
                                  target_id=loc.public_id,
                                  detail=f"v{loc.work_version} d{pub.disclosure_version}")
    return pub


def withdraw(location_id: int, actor) -> None:
    """Sofort wirksamer Rueckzug fuer neue Abrufe (auch bei frueheren Buchungen)."""
    with transaction.atomic():
        pub = PublishedLocation.objects.select_for_update().filter(location_id=location_id).first()
        if pub is None or not pub.is_active:
            return
        pub.is_active = False
        pub.withdrawn_at = timezone.now()
        pub.save(update_fields=["is_active", "withdrawn_at"])
        AuditEvent.objects.create(actor=actor, action="withdraw", target_type="location",
                                  target_id=pub.public_id)


# --- Arbeitsfassung (Portal/Redaktion) --------------------------------------

def save_work_version(location_id: int, actor, expected_work_version: int, changes: dict) -> Location:
    """Speichert Aenderungen nur, wenn die Arbeitsfassung unveraendert ist (Versionsvergleich).

    Jede Aenderung erhoeht work_version; eine eingereichte/freigegebene Fassung wird
    wieder Entwurf und muss neu eingereicht und geprueft werden. Der veroeffentlichte
    Stand bleibt unveraendert.
    """
    with transaction.atomic():
        loc = Location.objects.select_for_update().get(pk=location_id)
        if loc.work_version != expected_work_version:
            raise VersionConflict("Die Arbeitsfassung wurde inzwischen geaendert")
        for field, value in changes.items():
            setattr(loc, field, value)
        loc.work_version += 1
        loc.updated_by = actor
        if loc.review_state in (Location.ReviewState.SUBMITTED, Location.ReviewState.APPROVED):
            loc.review_state = Location.ReviewState.DRAFT
        loc.save()
        AuditEvent.objects.create(actor=actor, action="edit", target_type="location",
                                  target_id=loc.public_id, detail=f"v{loc.work_version}")
    return loc


def mark_work_changed(location_id: int, actor) -> None:
    """Foto-/Nebenaenderungen: neue Arbeitsversion, erneute Einreichung noetig."""
    from django.db.models import F

    Location.objects.filter(pk=location_id).update(work_version=F("work_version") + 1, updated_by=actor)
    Location.objects.filter(
        pk=location_id, review_state__in=[Location.ReviewState.SUBMITTED, Location.ReviewState.APPROVED]
    ).update(review_state=Location.ReviewState.DRAFT)


def reject(location_id: int, reviewer, expected_work_version: int, feedback: str) -> Location:
    with transaction.atomic():
        loc = Location.objects.select_for_update().get(pk=location_id)
        if loc.work_version != expected_work_version:
            raise VersionConflict("Arbeitsfassung wurde seit der Pruefung geaendert")
        if loc.review_state != Location.ReviewState.SUBMITTED:
            raise PublishError("Nur eingereichte Fassungen koennen zurueckgewiesen werden")
        loc.review_state = Location.ReviewState.REJECTED
        loc.review_feedback = feedback[:2000]
        loc.reviewed_at = timezone.now()
        loc.save(update_fields=["review_state", "review_feedback", "reviewed_at", "updated_at"])
        AuditEvent.objects.create(actor=reviewer, action="reject", target_type="location",
                                  target_id=loc.public_id, detail=f"v{loc.work_version}")
    return loc


def withdraw_contacts(location_id: int, actor) -> None:
    """Sofortiger Kontaktrueckzug: Arbeitsfassung und veroeffentlichter Stand.

    Neue disclosure_version, damit fruehere Buchungen nichts mehr ausliefern.
    """
    with transaction.atomic():
        loc = Location.objects.select_for_update().get(pk=location_id)
        loc.contacts_released = False
        loc.save(update_fields=["contacts_released", "updated_at"])
        pub = PublishedLocation.objects.select_for_update().filter(location=loc).first()
        if pub is not None and any(getattr(pub, f) for f in CONTACT_FIELDS):
            for f in CONTACT_FIELDS:
                setattr(pub, f, "")
            pub.disclosure_version += 1
            pub.save()
        AuditEvent.objects.create(actor=actor, action="withdraw_contacts", target_type="location",
                                  target_id=loc.public_id)


def withdraw_photo(photo_id: int, actor) -> None:
    with transaction.atomic():
        photo = Photo.objects.select_for_update().select_related("location").get(pk=photo_id)
        if photo.is_withdrawn:
            return
        photo.is_withdrawn = True
        photo.save(update_fields=["is_withdrawn"])
        AuditEvent.objects.create(actor=actor, action="withdraw_photo", target_type="photo",
                                  target_id=photo.public_id)
