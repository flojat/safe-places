"""Einreichen, Freigeben und Zurueckziehen mit kurzen Transaktionen und Versionsvergleich.

Berechtigungspruefung erfolgt im aufrufenden View/Admin ueber die Policies
(Etappe 4); dieser Service sichert Konsistenz und Atomaritaet.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from ..geo import CoordinateError, coarse_preview_point, validate_swiss_point
from ..models import AuditEvent, Location, Photo, PublishedLocation, PublishedPhoto, normalize_categories


class PublishError(Exception):
    pass


class VersionConflict(PublishError):
    pass


def missing_for_publication(loc: Location) -> list[str]:
    missing = []
    if not loc.title.strip():
        missing.append("Titel")
    if not normalize_categories(loc.categories):
        missing.append("Kategorie")
    for field, label in (
        ("preview_text", "Vorschautext"), ("place_text", "Ort/Region"), ("long_description", "Langbeschreibung"),
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


GUEST_RELEASE_FIELDS = ("guest_long_description_released", "guest_details_released", "guest_contacts_released")
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
        "title": loc.title, "categories": normalize_categories(loc.categories), "preview_text": loc.preview_text,
        "place_text": loc.place_text, "preview_lat": p_lat, "preview_lon": p_lon,
        "long_description": loc.long_description, "details": loc.details,
        "member_address": member_address, "member_lat": member_lat, "member_lon": member_lon,
        **contact, "is_demo": loc.is_demo,
        **{f: bool(getattr(loc, f)) for f in GUEST_RELEASE_FIELDS},
    }
    photos = list(
        loc.photos.filter(is_withdrawn=False)
        .exclude(visibility=Photo.Visibility.INTERNAL)
        .order_by("sort_order", "id")
    )
    return values, photos


# Bereiche fuer Mitteilungen an die Betreuung (nur Namen, nie Inhalte).
SECTIONS = (
    ("Vorschau (Titel, Kategorie, Text, Ort)", ("title", "categories", "preview_text", "place_text")),
    ("Vorschaulage", ("preview_lat", "preview_lon")),
    ("Beschreibung und Details", ("long_description", "details")),
    ("Genaue Lage fuer Mitglieder", ("member_address", "member_lat", "member_lon")),
    ("Kontaktangaben", CONTACT_FIELDS),
    ("Freigaben fuer Besucher", GUEST_RELEASE_FIELDS),
)
PHOTO_SECTION = "Fotos"
FIRST_PUBLICATION = "Erstveroeffentlichung"


def _norm(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (Decimal, float, int)) and not isinstance(value, bool):
        try:
            return format(Decimal(str(value)).normalize(), "f")
        except InvalidOperation:
            return str(value)
    return str(value)


def changed_sections(pub, values: dict, photos: list) -> list[str]:
    """Bereiche, in denen sich der neue Stand vom bisher veroeffentlichten unterscheidet."""
    if pub is None:
        return [FIRST_PUBLICATION]
    out = [label for label, fields in SECTIONS
           if any(_norm(getattr(pub, f)) != _norm(values.get(f)) for f in fields)]
    old = list(pub.photos.order_by("sort_order", "id")
               .values_list("photo_id", "visibility", "alt_text", "sort_order"))
    new = [(p.id, p.visibility, p.alt_text, p.sort_order) for p in photos]
    if old != new:
        out.append(PHOTO_SECTION)
    return out


def submit(location_id: int, actor, expected_work_version: int) -> Location:
    with transaction.atomic():
        loc = Location.objects.select_for_update().get(pk=location_id)
        if loc.work_version != expected_work_version:
            raise VersionConflict("Arbeitsfassung wurde inzwischen geaendert")
        if loc.review_state == Location.ReviewState.SUBMITTED:
            return loc
        loc.review_state = Location.ReviewState.SUBMITTED
        loc.submitted_at = timezone.now()
        loc.owner_changes_pending = False
        loc.save(update_fields=["review_state", "submitted_at", "owner_changes_pending", "updated_at"])
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
        pub, _sections = _publish_locked(loc, reviewer, action="publish")
    return pub


def editorial_publish(location_id: int, reviewer, expected_work_version: int):
    """Freigabe durch Redaktion/Administration (Verwaltung).

    - Immer eine zugeordnete Betreuung noetig.
    - Eingereichte Fassung: regulaere Freigabe.
    - Sonst Direktfreigabe: nur bei bereits aktiv veroeffentlichten Objekten oder wenn der interne
      Standard-Betreuer zugeordnet ist, und nie bei offenen Betreuungsaenderungen.
    Gibt (Snapshot, Mitteilungsart, geaenderte Bereiche) zurueck.
    """
    from ..models import LocationNotice

    with transaction.atomic():
        loc = Location.objects.select_for_update().get(pk=location_id)
        if loc.work_version != expected_work_version:
            raise VersionConflict("Arbeitsfassung wurde seit der Pruefung geaendert")
        blocker = editorial_publish_blocker(loc)
        if blocker:
            raise PublishError(blocker)
        if loc.review_state == Location.ReviewState.SUBMITTED:
            pub, sections = _publish_locked(loc, reviewer, action="publish")
            return pub, LocationNotice.Kind.PUBLISHED, sections
        pub, sections = _publish_locked(loc, reviewer, action="direct_publish")
        return pub, LocationNotice.Kind.DIRECT_PUBLISHED, sections


def editorial_publish_blocker(loc: Location) -> str:
    """Grund, warum Redaktion/Administration diese Fassung nicht freigeben darf ('' = erlaubt).

    Pflichtfelder prueft die Freigabe selbst (missing_for_publication).
    """
    from ..roles import is_default_object_manager

    if loc.managed_by_id is None:
        return "Keine Betreuung zugeordnet. Bitte zuerst eine Betreuung zuordnen."
    if loc.review_state == Location.ReviewState.SUBMITTED:
        return ""
    pub = PublishedLocation.objects.filter(location=loc).first()
    if pub is not None and pub.is_active and pub.source_work_version == loc.work_version:
        return "Diese Fassung ist bereits veroeffentlicht."
    if pub is not None and not pub.is_active:
        return ("Zurueckgezogene Objekte werden erst nach erneuter Einreichung durch die Betreuung "
                "wieder veroeffentlicht.")
    if pub is None and not is_default_object_manager(loc.managed_by):
        return "Noch nie veroeffentlicht: Freigabe erst nach Einreichung durch die Betreuung."
    if loc.owner_changes_pending:
        return ("Die Betreuung hat unveroeffentlichte Aenderungen. Direktfreigabe gesperrt, "
                "bis sie die Fassung einreicht.")
    return ""


def _publish_locked(loc: Location, reviewer, *, action: str) -> tuple[PublishedLocation, list[str]]:
    """Kern der Freigabe. Aufrufer haelt die Zeilensperre und hat Zustand/Version geprueft."""
    missing = missing_for_publication(loc)
    if missing:
        raise PublishError("Unvollstaendig: " + ", ".join(missing))

    values, photos = snapshot_values(loc)
    member_photo_ids = [p.id for p in photos if p.visibility == Photo.Visibility.MEMBER_DETAIL]

    now = timezone.now()
    pub = PublishedLocation.objects.select_for_update().filter(location=loc).first()
    sections = changed_sections(pub, values, photos)
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
    loc.owner_changes_pending = False
    loc.save(update_fields=["review_state", "reviewed_at", "owner_changes_pending", "updated_at"])
    AuditEvent.objects.create(actor=reviewer, action=action, target_type="location",
                              target_id=loc.public_id,
                              detail=f"v{loc.work_version} d{pub.disclosure_version}")
    return pub, sections


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
        if actor is not None and loc.managed_by_id == getattr(actor, "pk", None):
            loc.owner_changes_pending = True  # Betreuungsaenderung: Direktfreigabe bis Einreichung gesperrt
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
    if actor is not None:
        Location.objects.filter(pk=location_id, managed_by_id=actor.pk).update(owner_changes_pending=True)
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
        loc.guest_contacts_released = False
        loc.save(update_fields=["contacts_released", "guest_contacts_released", "updated_at"])
        pub = PublishedLocation.objects.select_for_update().filter(location=loc).first()
        if pub is not None and (pub.guest_contacts_released or any(getattr(pub, f) for f in CONTACT_FIELDS)):
            for f in CONTACT_FIELDS:
                setattr(pub, f, "")
            pub.guest_contacts_released = False
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
