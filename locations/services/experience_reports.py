"""Erfahrungsberichte von Mitgliedern zu Objekten (docs/ADR_ERFAHRUNGSBERICHTE.md).

Ablauf:
- Erfassen: vollstaendig verifizierte Mitglieder (inkl. Redaktion) zu einem aktiv veroeffentlichten
  Objekt; max. 2000 Zeichen und 3 Fotos (1 Ueberblick, 2 Details). Fotos nur mit ausdruecklicher
  Publikationsberechtigung (ausschliesslich diese Webseite, keine fremden Bildrechte).
- Erfasst ein Mitglied (nicht die Redaktion), erhaelt die Redaktion einen Hinweis per Mail.
- Freigeben, Zurueckweisen, Zurueckziehen nur durch die Redaktion; bei der ersten Freigabe
  erhaelt die Objektbetreuung eine Mitteilung (Portal + Mail, services.owner_notices).
- Sichtbar fuer Mitglieder mit gueltiger Freigabebuchung des Objekts; nie fuer Gaeste,
  nie mit Autorenangaben.
- Mails ohne Links und ohne Berichtsinhalt; eigenes Budget (report_mail_*).

Bewusst getrennt von kuenftigen Bewertungen: kein gemeinsames Modell, kein gemeinsamer Ablauf.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .. import rate_limits
from ..models import (
    EXPERIENCE_REPORT_MAX_CHARS, AuditEvent, ExperienceReport, ExperienceReportPhoto, Location,
    LocationNotice, PublishedLocation,
)
from ..roles import EDITOR
from . import images, owner_notices, security_mail

logger = logging.getLogger("locations.experience_reports")

State = ExperienceReport.State


class ReportRejected(ValueError):
    """Eingabe ungueltig (Text, Fotos, Berechtigung); Meldung ist fuer Benutzer bestimmt."""


class StateConflict(Exception):
    """Aktion passt nicht (mehr) zum aktuellen Zustand des Berichts."""


@dataclass(frozen=True)
class PhotoInput:
    kind: str
    upload: object
    alt_text: str = ""


def normalize_text(value: str) -> str:
    """Zeilenumbrueche vereinheitlichen (Browser senden CRLF), Rand-Leerraum entfernen."""
    return (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def validate_text(value: str) -> str:
    text = normalize_text(value)
    if not text:
        raise ReportRejected("Bitte einen Erfahrungsbericht eingeben.")
    if len(text) > EXPERIENCE_REPORT_MAX_CHARS:
        raise ReportRejected(f"Der Erfahrungsbericht ist zu lang (max. {EXPERIENCE_REPORT_MAX_CHARS} Zeichen).")
    return text


def validate_photo_set(photos: list[PhotoInput]) -> None:
    if len(photos) > settings.EXPERIENCE_REPORT_MAX_PHOTOS:
        raise ReportRejected(f"Hoechstens {settings.EXPERIENCE_REPORT_MAX_PHOTOS} Fotos pro Bericht.")
    kinds = [p.kind for p in photos]
    if kinds.count(ExperienceReportPhoto.Kind.OVERVIEW) > 1:
        raise ReportRejected("Hoechstens ein Ueberblicksfoto pro Bericht.")
    if any(k not in ExperienceReportPhoto.Kind.values for k in kinds):
        raise ReportRejected("Unbekannte Fotoart.")
    for p in photos:
        if p.upload.size > settings.IMAGE_MAX_UPLOAD_BYTES:
            raise ReportRejected("Ein Foto ist zu gross (max. 10 MiB).")


def _is_editor(user) -> bool:
    from ..roles import is_admin, is_editor

    return is_editor(user) or is_admin(user)


def submit(location_id: int, author, text: str, photos: list[PhotoInput], *, photo_consent: bool) -> ExperienceReport:
    """Legt einen Bericht im Zustand "eingereicht" an. Der Aufrufer prueft Sitzung und Limits."""
    text = validate_text(text)
    validate_photo_set(photos)
    if photos and not photo_consent:
        raise ReportRejected("Ohne ausdrueckliche Publikationsberechtigung koennen keine Fotos hochgeladen werden.")

    processed: list[tuple[PhotoInput, images.ProcessedImage]] = []
    try:
        for p in photos:
            data = p.upload.read(settings.IMAGE_MAX_UPLOAD_BYTES + 1)
            try:
                processed.append((p, images.process_upload(data)))
            except images.ImageRejected as exc:
                raise ReportRejected(f"Foto abgelehnt: {exc}") from exc
        with transaction.atomic():
            loc = Location.objects.select_for_update().get(pk=location_id)
            if not PublishedLocation.objects.filter(location=loc, is_active=True).exists():
                raise ReportRejected("Zu diesem Objekt koennen derzeit keine Erfahrungsberichte erfasst werden.")
            now = timezone.now()
            report = ExperienceReport.objects.create(
                location=loc, author=author, text=text, photo_consent_at=now if photos else None,
            )
            order = sorted(processed, key=lambda item: item[0].kind != ExperienceReportPhoto.Kind.OVERVIEW)
            for index, (p, img) in enumerate(order):
                ExperienceReportPhoto.objects.create(
                    report=report, kind=p.kind, storage_key=img.storage_key, alt_text=(p.alt_text or "")[:200],
                    sort_order=index, rights_confirmed_at=now, width=img.width, height=img.height,
                )
            AuditEvent.objects.create(actor=author, action="report_submit", target_type="report",
                                      target_id=report.public_id,
                                      detail=f"fotos={len(photos)}" + (" publikationsberechtigung" if photos else ""))
    except Exception:
        for _p, img in processed:
            images.delete_files(img.storage_key)
        raise

    if not _is_editor(author):
        report.editor_mail_status = notify_editors(report)
        report.save(update_fields=["editor_mail_status"])
    return report


# --- Hinweis an die Redaktion ----------------------------------------------------

def editor_recipients(exclude_user_id=None) -> list:
    """Aktive Konten der Redaktion mit bestaetigter Adresse; ohne Redaktion: Administration."""
    User = get_user_model()
    base = User.objects.filter(is_active=True, state=User.State.ACTIVE, email_verified_at__isnull=False,
                               is_staff=True).exclude(pk=exclude_user_id).order_by("pk")
    editors = list(base.filter(groups__name=EDITOR).distinct())
    return editors or list(base.filter(is_superuser=True))


def notify_editors(report: ExperienceReport) -> str:
    recipients = editor_recipients(exclude_user_id=report.author_id)
    if not recipients:
        return ExperienceReport.MailStatus.NO_RECIPIENT
    body = render_to_string("emails/report_submitted.txt", {
        "title": str(report.location), "report_id": report.public_id,
        "photo_count": report.photos.count(), "length": len(report.text),
    })
    sent = failed = budget = 0
    for user in recipients:
        try:
            rate_limits.reserve([
                *rate_limits.configured("report_mail_recipient", str(user.pk)),
                *rate_limits.configured("report_mail_global", "global"),
            ])
        except (rate_limits.RateLimited, rate_limits.RateLimiterUnavailable):
            budget += 1
            continue
        if security_mail.deliver(purpose="report-submitted", recipient=user.email,
                                 subject="Adresskarte: Neuer Erfahrungsbericht zur Pruefung", body=body):
            sent += 1
        else:
            failed += 1
    if sent == len(recipients):
        return ExperienceReport.MailStatus.SENT
    if sent:
        return ExperienceReport.MailStatus.PARTIAL
    if budget:
        logger.info("Hinweis an Redaktion nicht gesendet (Budget): report=%s", report.public_id)
        return ExperienceReport.MailStatus.BUDGET
    return ExperienceReport.MailStatus.FAILED


# --- Redaktionelle Entscheide -------------------------------------------------------

def _locked(report_id: int) -> ExperienceReport:
    return ExperienceReport.objects.select_for_update().get(pk=report_id)


def publish(report_id: int, actor) -> tuple[ExperienceReport, LocationNotice | None]:
    """Freigabe. Bei der ersten Freigabe wird die Objektbetreuung informiert."""
    with transaction.atomic():
        report = _locked(report_id)
        if report.state not in (State.SUBMITTED, State.WITHDRAWN):
            raise StateConflict("Nur eingereichte oder zurueckgezogene Berichte koennen freigegeben werden.")
        first = report.published_at is None
        now = timezone.now()
        report.state = State.PUBLISHED
        report.reviewed_by, report.reviewed_at = actor, now
        if first:
            report.published_at = now
        report.save(update_fields=["state", "reviewed_by", "reviewed_at", "published_at", "updated_at"])
        AuditEvent.objects.create(actor=actor, action="report_publish", target_type="report",
                                  target_id=report.public_id, detail="erstfreigabe" if first else "wieder")
    notice = None
    if first:
        notice = owner_notices.record(report.location, LocationNotice.Kind.REPORT_PUBLISHED, actor)
    return report, notice


def reject(report_id: int, actor, feedback: str = "") -> ExperienceReport:
    """Zurueckweisung eines eingereichten Berichts; Fotodateien werden geloescht."""
    with transaction.atomic():
        report = _locked(report_id)
        if report.state != State.SUBMITTED:
            raise StateConflict("Nur eingereichte Berichte koennen zurueckgewiesen werden.")
        report.state = State.REJECTED
        report.review_feedback = (feedback or "")[:2000]
        report.reviewed_by, report.reviewed_at = actor, timezone.now()
        report.save(update_fields=["state", "review_feedback", "reviewed_by", "reviewed_at", "updated_at"])
        keys = _remove_photos(report.photos.filter(is_removed=False))
        AuditEvent.objects.create(actor=actor, action="report_reject", target_type="report",
                                  target_id=report.public_id)
        transaction.on_commit(lambda: [images.delete_files(k) for k in keys])
    return report


def withdraw(report_id: int, actor) -> ExperienceReport:
    """Rueckzug eines veroeffentlichten Berichts; sofort fuer neue Abrufe unsichtbar."""
    with transaction.atomic():
        report = _locked(report_id)
        if report.state != State.PUBLISHED:
            raise StateConflict("Nur veroeffentlichte Berichte koennen zurueckgezogen werden.")
        report.state = State.WITHDRAWN
        report.reviewed_by, report.reviewed_at = actor, timezone.now()
        report.save(update_fields=["state", "reviewed_by", "reviewed_at", "updated_at"])
        AuditEvent.objects.create(actor=actor, action="report_withdraw", target_type="report",
                                  target_id=report.public_id)
    return report


def edit_text(report_id: int, actor, text: str) -> bool:
    """Redaktionelle Korrektur des Textes (z. B. Personendaten entfernen). True bei Aenderung."""
    text = validate_text(text)
    with transaction.atomic():
        report = _locked(report_id)
        if report.state == State.REJECTED:
            raise StateConflict("Zurueckgewiesene Berichte werden nicht mehr bearbeitet.")
        if report.text == text:
            return False
        report.text = text
        report.save(update_fields=["text", "updated_at"])
        AuditEvent.objects.create(actor=actor, action="report_edit", target_type="report",
                                  target_id=report.public_id, detail=f"zeichen={len(text)}")
    return True


def remove_photo(photo_id: int, actor) -> None:
    """Entfernt ein Berichtsfoto sofort (auch bei veroeffentlichten Berichten) samt Dateien."""
    with transaction.atomic():
        photo = ExperienceReportPhoto.objects.select_for_update().get(pk=photo_id)
        if photo.is_removed:
            return
        keys = _remove_photos([photo])
        AuditEvent.objects.create(actor=actor, action="report_photo_remove", target_type="report_photo",
                                  target_id=photo.public_id)
        transaction.on_commit(lambda: [images.delete_files(k) for k in keys])


def _remove_photos(photos) -> list[str]:
    keys = []
    for photo in photos:
        keys.append(photo.storage_key)
        ExperienceReportPhoto.objects.filter(pk=photo.pk).update(is_removed=True)
    return keys


# --- Ausgabe ---------------------------------------------------------------------

def photo_url(photo: ExperienceReportPhoto, variant: str) -> str:
    return reverse("locations:report_photo", args=[photo.public_id, "vorschau" if variant == "thumb" else "detail"])


def published_reports(pub: PublishedLocation) -> list[dict]:
    """Veroeffentlichte Berichte fuer die Mitgliedsansicht; ohne Autorenangaben.

    Der Aufrufer hat die gueltige Freigabebuchung bereits geprueft.
    """
    if not pub.is_active:
        return []
    reports = (ExperienceReport.objects.filter(location_id=pub.location_id, state=State.PUBLISHED)
               .order_by("-published_at", "-id").prefetch_related("photos"))
    out = []
    for r in reports:
        photos = [p for p in r.photos.all() if not p.is_removed]
        out.append({
            "id": r.public_id, "text": r.text, "published_at": r.published_at,
            "gallery": [{"thumb": photo_url(p, "thumb"), "full": photo_url(p, "detail"),
                         "description": p.alt_text} for p in photos],
        })
    return out


def photo_access(request, photo: ExperienceReportPhoto) -> bool:
    """Bei jedem Abruf neu: Redaktion/Administration, Autor oder Mitglied mit gueltiger Buchung."""
    from ..member_access import valid_grant
    from ..permissions import is_fully_verified

    if photo.is_removed or not is_fully_verified(request):
        return False
    report = photo.report
    if _is_editor(request.user) or report.author_id == request.user.pk:
        return True
    if report.state != State.PUBLISHED:
        return False
    pub = PublishedLocation.objects.filter(location_id=report.location_id, is_active=True).first()
    return pub is not None and valid_grant(request, pub) is not None
