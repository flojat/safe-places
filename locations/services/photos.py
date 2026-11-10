"""Gemeinsamer Upload-/Loeschweg fuer Fotos der Arbeitsfassung (Portal und Verwaltung).

Bildpruefung und Varianten ueber services.images; Limits und Rechte prueft der Aufrufer.
Freigegebene Bilddateien werden nie ueberschrieben: Ersetzen = neues Bild + altes entfernen.
"""

from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import AuditEvent, Location, Photo, PublishedPhoto
from . import images, publishing


class PhotoRejected(ValueError):
    pass


class AccessLost(Exception):
    """Berechtigung waehrend der Verarbeitung entzogen (z. B. Betreuung neu zugeordnet)."""


def add_photo(location_id: int, upload, *, alt_text: str, visibility: str, actor, rights_confirmed: bool,
              still_allowed=None) -> Photo:
    if not rights_confirmed:
        raise PhotoRejected("Ohne Bestaetigung der Bildrechte ist kein Hochladen moeglich.")
    loc = Location.objects.get(pk=location_id)
    if loc.photos.filter(is_withdrawn=False).count() >= settings.MAX_ACTIVE_PHOTOS_PER_LOCATION:
        raise PhotoRejected("Maximale Anzahl aktiver Bilder erreicht.")
    if upload.size > settings.IMAGE_MAX_UPLOAD_BYTES:
        raise PhotoRejected("Datei zu gross.")
    data = upload.read(settings.IMAGE_MAX_UPLOAD_BYTES + 1)
    try:
        processed = images.process_upload(data)
    except images.ImageRejected as exc:
        raise PhotoRejected(f"Bild abgelehnt: {exc}") from exc
    with transaction.atomic():
        locked = Location.objects.select_for_update().get(pk=location_id)
        if still_allowed is not None and not still_allowed(locked):
            images.delete_files(processed.storage_key)
            raise AccessLost()
        photo = Photo.objects.create(
            location=locked, storage_key=processed.storage_key, alt_text=alt_text,
            visibility=visibility, width=processed.width, height=processed.height,
            uploaded_by=actor, sort_order=locked.photos.count(), rights_confirmed_at=timezone.now(),
        )
        AuditEvent.objects.create(actor=actor, action="photo_upload", target_type="photo",
                                  target_id=photo.public_id, detail="bildrechte_bestaetigt")
        publishing.mark_work_changed(locked.pk, actor)
    return photo


def remove_photo(photo_id: int, actor) -> bool:
    """Entfernt ein Foto aus der Arbeitsfassung.

    Ist es Teil eines freigegebenen Stands, wird es sofort ausgeblendet (zurueckgezogen) und bleibt
    als Datensatz erhalten, bis kein Snapshot mehr darauf verweist; sonst wird es samt Dateien
    geloescht. Gibt True zurueck, wenn ein veroeffentlichtes Bild betroffen war.
    """
    with transaction.atomic():
        photo = Photo.objects.select_for_update().get(pk=photo_id)
        referenced = PublishedPhoto.objects.filter(photo=photo).exists()
        if referenced:
            was_public = PublishedPhoto.objects.filter(photo=photo, published__is_active=True).exists() \
                and not photo.is_withdrawn
            if not photo.is_withdrawn:
                photo.is_withdrawn = True
                photo.save(update_fields=["is_withdrawn"])
            AuditEvent.objects.create(actor=actor, action="photo_remove_withdrawn", target_type="photo",
                                      target_id=photo.public_id)
            publishing.mark_work_changed(photo.location_id, actor)
            return was_public
        storage_key, public_id, location_id = photo.storage_key, photo.public_id, photo.location_id
        photo.delete()
        AuditEvent.objects.create(actor=actor, action="photo_delete", target_type="photo", target_id=public_id)
        publishing.mark_work_changed(location_id, actor)
        transaction.on_commit(lambda: images.delete_files(storage_key))
    return False
