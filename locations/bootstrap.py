"""Initialkonten: Provisionierung, Einladung, Aktivierung, Rollenfreischaltung (Abschnitt 11).

- Manifest nur lokal/privat (initial_accounts.local.json, nicht in Git).
- Neue Konten: unbrauchbares Passwort, unbestaetigte E-Mail, Zustand pending_activation.
- Idempotent und transaktional; nie Reset, Entsperren, neue Rolle oder Wiederherstellung
  bei Wiederholung. Fremd registrierte Adresse = Konflikt ohne Privilegien.
- Einladung: an Konto, Adresse, Zweck, Umgebung, Einladungs- und Sicherheitsgeneration
  gebunden, 24 h, einmalig; GET verbraucht nichts. Neue Einladung invalidiert alte.
- Rolle erst nach Aktivierung UND eingerichtetem zweiten Schritt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import signing
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from .models import AuditEvent, InitialAccount, normalize_email_address
from .roles import EDITOR, OBJECT_MANAGER, ensure_role_groups
from .services import security_mail

INVITE_SALT = "adresskarte.initial-invitation"
INVITE_PURPOSE = "activate-initial-account"
SESSION_INVITE = "pending_invitation"
REQUIRED_ROLES = {r.value for r in InitialAccount.Role}


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class ManifestEntry:
    email: str
    role: str


def load_manifest(path: str | Path) -> list[ManifestEntry]:
    """Format: {"accounts": [{"email": "...", "role": "visitor|object_manager|editor|admin"}]}
    Genau vier Eintraege, jede Rolle genau einmal. Fehlermeldungen nennen keine Adressen."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError("Manifest nicht lesbar oder kein JSON") from exc
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if not k.startswith("_")}  # Kommentarfelder
    accounts = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(accounts, list) or len(accounts) != 4:
        raise ManifestError("Manifest muss genau vier Konten enthalten")
    entries, emails = [], set()
    for i, item in enumerate(accounts, start=1):
        if not isinstance(item, dict) or set(item) - {"email", "role"}:
            raise ManifestError(f"Eintrag {i}: nur Felder email und role erlaubt")
        email = normalize_email_address(str(item.get("email", "")))
        role = str(item.get("role", ""))
        if "@" not in email or len(email) > 254:
            raise ManifestError(f"Eintrag {i}: ungueltige E-Mail")
        if role not in REQUIRED_ROLES:
            raise ManifestError(f"Eintrag {i}: unbekannte Rolle")
        if email in emails:
            raise ManifestError(f"Eintrag {i}: doppelte E-Mail")
        emails.add(email)
        entries.append(ManifestEntry(email, role))
    if {e.role for e in entries} != REQUIRED_ROLES:
        raise ManifestError("Jede der vier Rollen muss genau einmal vorkommen")
    return entries


@dataclass(frozen=True)
class PlanItem:
    role: str
    action: str  # create | unchanged | conflict | removed


def plan(entries: list[ManifestEntry]) -> list[PlanItem]:
    User = get_user_model()
    items = []
    for entry in entries:
        record = InitialAccount.objects.filter(email=entry.email).first()
        if record is not None:
            action = "removed" if record.user_id is None and record.state != InitialAccount.State.CONFLICT else (
                "conflict" if record.state == InitialAccount.State.CONFLICT else "unchanged")
        elif User.objects.filter(email=entry.email).exists():
            action = "conflict"
        else:
            action = "create"
        items.append(PlanItem(entry.role, action))
    return items


def apply(entries: list[ManifestEntry]) -> list[PlanItem]:
    """Legt fehlende Konten an. Bestehende Bootstrap-Konten bleiben unveraendert."""
    User = get_user_model()
    results = []
    for entry in entries:
        try:
            with transaction.atomic():
                record = InitialAccount.objects.select_for_update().filter(email=entry.email).first()
                if record is not None:
                    action = "conflict" if record.state == InitialAccount.State.CONFLICT else (
                        "removed" if record.user_id is None else "unchanged")
                elif User.objects.filter(email=entry.email).exists():
                    # Vorhandene Registrierung o. ae.: keine Privilegien, nur Konflikt vermerken.
                    InitialAccount.objects.create(
                        email=entry.email, target_role=entry.role, state=InitialAccount.State.CONFLICT,
                        environment=settings.ENVIRONMENT, provisioned_at=timezone.now(),
                    )
                    action = "conflict"
                else:
                    user = User.objects.create_user(entry.email, None)  # unbrauchbares Passwort
                    user.state = User.State.PENDING_ACTIVATION
                    user.origin = User.Origin.BOOTSTRAP
                    user.save(update_fields=["state", "origin"])
                    InitialAccount.objects.create(
                        email=entry.email, user=user, target_role=entry.role,
                        environment=settings.ENVIRONMENT, provisioned_at=timezone.now(),
                    )
                    AuditEvent.objects.create(actor=None, action="bootstrap_provisioned", target_type="user",
                                              target_id=str(user.pk), detail=entry.role)
                    action = "create"
        except IntegrityError:
            action = "unchanged"  # paralleler Lauf hat angelegt
        results.append(PlanItem(entry.role, action))
    return results


# --- Einladung -------------------------------------------------------------------

def _fingerprint(email: str) -> str:
    return salted_hmac(INVITE_SALT, normalize_email_address(email), algorithm="sha256").hexdigest()[:32]


def _invitable(record: InitialAccount) -> bool:
    user = record.user
    return (
        user is not None
        and record.state in (InitialAccount.State.PROVISIONED, InitialAccount.State.INVITED)
        and user.state == user.State.PENDING_ACTIVATION
        and not user.has_usable_password()
    )


def invitation_candidates():
    return [r for r in InitialAccount.objects.select_related("user").order_by("target_role") if _invitable(r)]


def send_invitation(record_id: int, base_url: str) -> bool:
    """Neue Einladung (alte wird ungueltig). Mailbudget gilt. Gibt True bei Versand zurueck."""
    with transaction.atomic():
        record = InitialAccount.objects.select_for_update().select_related("user").get(pk=record_id)
        if not _invitable(record):
            return False
        record.invitation_generation += 1
        record.state = InitialAccount.State.INVITED
        record.invited_at = timezone.now()
        record.save(update_fields=["invitation_generation", "state", "invited_at"])
        payload = {
            "u": record.user_id, "e": _fingerprint(record.email), "ig": record.invitation_generation,
            "g": record.user.security_generation, "p": INVITE_PURPOSE, "env": settings.ENVIRONMENT,
        }
        AuditEvent.objects.create(actor=None, action="bootstrap_invited", target_type="user",
                                  target_id=str(record.user_id), detail=f"g{record.invitation_generation}")
    token = signing.dumps(payload, salt=INVITE_SALT, compress=True)
    link = base_url.rstrip("/") + reverse("account:activate_token", args=[token])
    try:
        return security_mail.send(
            purpose="invitation", recipient=record.email, user=record.user,
            subject="Einladung: Konto für die Adresskarte aktivieren",
            body=render_to_string("emails/invitation.txt", {
                "link": link, "hours": settings.INVITATION_MAX_AGE // 3600,
            }),
        )
    except security_mail.MailBudgetExceeded:
        return False


class InvitationError(Exception):
    pass


def _load(token: str) -> dict:
    try:
        payload = signing.loads(token, salt=INVITE_SALT, max_age=settings.INVITATION_MAX_AGE)
    except signing.BadSignature as exc:
        raise InvitationError("ungueltig oder abgelaufen") from exc
    if payload.get("p") != INVITE_PURPOSE or payload.get("env") != settings.ENVIRONMENT:
        raise InvitationError("falscher Zweck oder falsche Umgebung")
    return payload


def _matches(record: InitialAccount | None, payload: dict) -> bool:
    return (
        record is not None and record.user is not None
        and record.user_id == payload.get("u")
        and record.state == InitialAccount.State.INVITED
        and record.invitation_generation == payload.get("ig")
        and record.user.security_generation == payload.get("g")
        and record.user.state == record.user.State.PENDING_ACTIVATION
        and constant_time_compare(_fingerprint(record.user.email), payload.get("e", ""))
    )


def check_invitation(token: str) -> InitialAccount:
    payload = _load(token)
    record = InitialAccount.objects.select_related("user").filter(user_id=payload.get("u")).first()
    if not _matches(record, payload):
        raise InvitationError("nicht mehr gueltig")
    return record


def accept_invitation(token: str, password: str) -> None:
    """Atomar und einmalig: Passwort setzen und genau die eingeladene Adresse bestaetigen.
    Keine Sitzung, kein zweiter Faktor, keine Rolle."""
    payload = _load(token)
    User = get_user_model()
    with transaction.atomic():
        record = InitialAccount.objects.select_for_update().filter(user_id=payload.get("u")).first()
        user = User.objects.select_for_update().filter(pk=payload.get("u")).first()
        if record is not None:
            record.user = user
        if not _matches(record, payload):
            raise InvitationError("nicht mehr gueltig")
        user.set_password(password)
        user.email_verified_at = timezone.now()
        user.state = User.State.ACTIVE
        user.security_generation += 1
        user.save(update_fields=["password", "email_verified_at", "state", "security_generation"])
        record.state = InitialAccount.State.ACTIVATED
        record.activated_at = timezone.now()
        record.invitation_generation += 1  # Token verbraucht
        record.save(update_fields=["state", "activated_at", "invitation_generation"])
        AuditEvent.objects.create(actor=user, action="bootstrap_activated", target_type="user",
                                  target_id=str(user.pk))


def finalize_roles(user) -> bool:
    """Nach eingerichtetem zweiten Schritt: hinterlegte Zielrolle genau einmal vergeben."""
    with transaction.atomic():
        record = InitialAccount.objects.select_for_update().filter(
            user=user, state=InitialAccount.State.ACTIVATED).first()
        if record is None:
            return False
        groups = ensure_role_groups()
        User = get_user_model()
        role = record.target_role
        updates = {}
        if role == InitialAccount.Role.OBJECT_MANAGER:
            user.groups.add(groups[OBJECT_MANAGER])
        elif role == InitialAccount.Role.EDITOR:
            user.groups.add(groups[EDITOR])
            updates = {"is_staff": True}
        elif role == InitialAccount.Role.ADMIN:
            updates = {"is_staff": True, "is_superuser": True}
        if updates:
            User.objects.filter(pk=user.pk).update(**updates)
            for k, v in updates.items():
                setattr(user, k, v)
        record.state = InitialAccount.State.COMPLETE
        record.completed_at = timezone.now()
        record.save(update_fields=["state", "completed_at"])
        AuditEvent.objects.create(actor=user, action="role_grant", target_type="user",
                                  target_id=str(user.pk), detail=f"bootstrap:{role}")
    return True


def admin_ready() -> bool:
    """Mindestens ein aktiver Administrator mit eingerichtetem zweiten Schritt."""
    from django_otp import devices_for_user

    User = get_user_model()
    for admin in User.objects.filter(is_superuser=True, is_active=True, state=User.State.ACTIVE,
                                     email_verified_at__isnull=False):
        if any(True for _ in devices_for_user(admin)):
            return True
    return False


def status_report() -> dict:
    """Teilbarer Bericht: nur Rollen und Zustaende, keine Adressen."""
    return {r.target_role: r.state for r in InitialAccount.objects.order_by("target_role")}
