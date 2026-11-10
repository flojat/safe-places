"""Mitteilungen an die Betreuung ueber redaktionelle Anpassungen und Entscheide.

- Jede Mitteilung wird gespeichert (Betreuerportal + Verwaltung).
- Entscheide (Freigabe, Direktfreigabe, Zurueckweisung, Rueckzug) zusaetzlich per Mail an die
  bestaetigte Adresse der zugeordneten Betreuung.
- Eigenes Mailbudget (notice_*), damit Sicherheitsmails (Login-Codes) nie verdraengt werden.
- Mails ohne Links (Phishing-Hygiene) und ohne Inhalte: nur Titel, Art und Bereichsnamen.
- Versandfehler blockieren die Aktion nicht; der Status steht an der Mitteilung.
"""

from __future__ import annotations

import logging

from django.template.loader import render_to_string

from .. import rate_limits
from ..models import Location, LocationNotice
from . import security_mail

logger = logging.getLogger("locations.owner_notices")

MAIL_KINDS = {
    LocationNotice.Kind.PUBLISHED,
    LocationNotice.Kind.DIRECT_PUBLISHED,
    LocationNotice.Kind.REJECTED,
    LocationNotice.Kind.WITHDRAWN,
    LocationNotice.Kind.PHOTO_REMOVED,
}


def record(loc: Location, kind: str, actor, *, sections=(), feedback: str = "") -> LocationNotice:
    """Speichert die Mitteilung und versendet bei Entscheiden eine Mail. Gibt die Mitteilung zurueck."""
    notice = LocationNotice.objects.create(
        location=loc, recipient_id=loc.managed_by_id, actor=actor, kind=kind,
        sections=", ".join(sections)[:300], feedback=(feedback or "")[:2000],
        work_version=loc.work_version,
    )
    if kind in MAIL_KINDS:
        notice.mail_status = _send(loc, notice)
        notice.save(update_fields=["mail_status"])
    return notice


def _send(loc: Location, notice: LocationNotice) -> str:
    user = loc.managed_by
    if user is None or not user.email or user.email_verified_at is None:
        return LocationNotice.MailStatus.NO_RECIPIENT
    try:
        rate_limits.reserve([
            *rate_limits.configured("notice_recipient", str(user.pk)),
            *rate_limits.configured("notice_global", "global"),
        ])
    except (rate_limits.RateLimited, rate_limits.RateLimiterUnavailable):
        logger.info("Betreuungsmitteilung nicht gesendet (Budget): kind=%s", notice.kind)
        return LocationNotice.MailStatus.BUDGET
    body = render_to_string("emails/location_notice.txt", {
        "title": str(loc), "kind_text": notice.get_kind_display(),
        "sections": notice.sections, "feedback": notice.feedback,
    })
    sent = security_mail.deliver(purpose="location-notice", recipient=user.email,
                                 subject=f"Adresskarte: {notice.get_kind_display()}", body=body)
    return LocationNotice.MailStatus.SENT if sent else LocationNotice.MailStatus.FAILED


def editor_edit_sections(changed_fields, photos_changed: bool) -> list[str]:
    """Bereichsnamen fuer eine redaktionelle Bearbeitung der Arbeitsfassung (ohne interne Felder)."""
    groups = (
        ("Vorschau (Titel, Kategorie, Text, Ort)", {"title", "categories", "preview_text", "place_text"}),
        ("Vorschaulage", {"preview_lat", "preview_lon"}),
        ("Beschreibung und Details", {"long_description", "details"}),
        ("Genaue Lage", {"exact_address", "exact_lat", "exact_lon", "member_exact_location_released"}),
        ("Kontaktangaben", {"contact_name", "contact_organisation", "contact_email", "contact_phone",
                            "contact_website", "contacts_released", "provider"}),
        ("Freigaben fuer Besucher", {"guest_long_description_released", "guest_details_released",
                                     "guest_contacts_released"}),
    )
    changed = set(changed_fields)
    out = [label for label, fields in groups if changed & fields]
    if photos_changed:
        out.append("Fotos")
    return out
