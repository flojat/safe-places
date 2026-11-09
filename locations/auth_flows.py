"""Registrierung, E-Mail-Verifikation und Bindung der Login-Codes (Abschnitt 8).

Kryptografie ausschliesslich aus Django (signing, salted_hmac, constant_time_compare)
und django-otp. Anwendungscode ist hier nur die Zustandsbindung.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core import signing
from django.db import transaction
from django.db.models import F
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django_otp.plugins.otp_email.models import EmailDevice

from . import rate_limits
from .middleware import SESSION_GENERATION
from .models import AuditEvent, LoginChallenge, normalize_email_address
from .services import security_mail

logger = logging.getLogger("locations.auth")

VERIFY_SALT = "adresskarte.email-verification"
VERIFY_PURPOSE = "verify-email"
SESSION_VERIFY_TOKEN = "pending_email_verification"
SESSION_CHALLENGE = "login_challenge"


# --- Sicherheitsgeneration ---------------------------------------------------

def rotate_security_generation(user, request=None) -> None:
    """Invalidiert alle anderen Sitzungen/Tokens; die aktuelle bleibt gueltig."""
    User = get_user_model()
    User.objects.filter(pk=user.pk).update(security_generation=F("security_generation") + 1)
    user.refresh_from_db(fields=["security_generation"])
    if request is not None and request.user.is_authenticated and request.user.pk == user.pk:
        request.session[SESSION_GENERATION] = user.security_generation
        request.session.cycle_key()


# --- E-Mail-Verifikation -----------------------------------------------------

def _email_fingerprint(email: str) -> str:
    return salted_hmac(VERIFY_SALT, normalize_email_address(email), algorithm="sha256").hexdigest()[:32]


def make_verification_token(user) -> str:
    payload = {
        "u": user.pk,
        "e": _email_fingerprint(user.email),
        "g": user.security_generation,
        "p": VERIFY_PURPOSE,
        "env": settings.ENVIRONMENT,
    }
    return signing.dumps(payload, salt=VERIFY_SALT, compress=True)


class VerificationError(Exception):
    pass


def _load_verification_payload(token: str) -> dict:
    try:
        payload = signing.loads(token, salt=VERIFY_SALT, max_age=settings.EMAIL_VERIFICATION_MAX_AGE)
    except signing.SignatureExpired as exc:
        raise VerificationError("abgelaufen") from exc
    except signing.BadSignature as exc:
        raise VerificationError("ungueltig") from exc
    if payload.get("p") != VERIFY_PURPOSE or payload.get("env") != settings.ENVIRONMENT:
        raise VerificationError("falscher Zweck oder falsche Umgebung")
    return payload


def check_verification_token(token: str):
    """Prueft ohne zu verbrauchen; liefert das Konto oder wirft VerificationError."""
    payload = _load_verification_payload(token)
    user = get_user_model().objects.filter(pk=payload.get("u")).first()
    if (
        user is None
        or user.state != user.State.PENDING_EMAIL
        or user.security_generation != payload.get("g")
        or not constant_time_compare(_email_fingerprint(user.email), payload.get("e", ""))
    ):
        raise VerificationError("nicht mehr gueltig")
    return user


def consume_verification_token(token: str):
    """Bestaetigt atomar genau einmal die aktuell zu pruefende Adresse.

    Aktiviert keine Sitzung; danach regulaerer Login mit zweitem Schritt.
    """
    payload = _load_verification_payload(token)
    User = get_user_model()
    with transaction.atomic():
        user = User.objects.select_for_update().filter(pk=payload.get("u")).first()
        if (
            user is None
            or user.state != User.State.PENDING_EMAIL
            or user.security_generation != payload.get("g")
            or not constant_time_compare(_email_fingerprint(user.email), payload.get("e", ""))
        ):
            raise VerificationError("nicht mehr gueltig")
        user.email_verified_at = timezone.now()
        user.state = User.State.ACTIVE
        user.security_generation += 1  # Token und evtl. Sitzungen danach ungueltig
        user.save(update_fields=["email_verified_at", "state", "security_generation"])
        AuditEvent.objects.create(actor=user, action="email_verified", target_type="user", target_id=str(user.pk))
    return user


def send_verification_mail(user, request) -> bool:
    token = make_verification_token(user)
    link = request.build_absolute_uri(reverse("account:verify_email_token", args=[token]))
    body = render_to_string("emails/verify_email.txt", {
        "link": link,
        "hours": settings.EMAIL_VERIFICATION_MAX_AGE // 3600,
    })
    return security_mail.send(
        purpose="verify", recipient=user.email, subject="Bitte bestätigen Sie Ihre E-Mail-Adresse",
        body=body, user=user, request=request,
    )


# --- Registrierung -----------------------------------------------------------

class RegistrationRejected(Exception):
    def __init__(self, retry_after: int):
        super().__init__("Registrierung voruebergehend nicht moeglich")
        self.retry_after = retry_after


def register(*, email: str, password: str, request) -> None:
    """Legt hoechstens ein neues pending-Besucherkonto an. Bestehende Konten werden
    nie veraendert. Die Antwort an den Browser ist in allen Faellen gleich."""
    try:
        rate_limits.reserve([
            *rate_limits.configured("register_ip", rate_limits.client_ip(request)),
            *rate_limits.configured("register_global", "global"),
        ])
    except rate_limits.RateLimited as exc:
        raise RegistrationRejected(exc.retry_after) from exc

    User = get_user_model()
    email = normalize_email_address(email)
    existing = User.objects.filter(email=email).first()
    if existing is not None:
        make_password(password)  # vergleichbarer Rechenaufwand, Ergebnis verworfen
        if existing.state == User.State.PENDING_EMAIL:
            _try_send_verification(existing, request)
        return
    with transaction.atomic():
        user = User.objects.create_user(email=email, password=password)  # pending_email, keine Rollen
        AuditEvent.objects.create(actor=None, action="registered", target_type="user", target_id=str(user.pk))
    _try_send_verification(user, request)


def resend_verification(*, email: str, request) -> None:
    rate_limits.reserve(rate_limits.configured("verify_resend_ip", rate_limits.client_ip(request)))
    User = get_user_model()
    user = User.objects.filter(email=normalize_email_address(email), state=User.State.PENDING_EMAIL).first()
    if user is not None:
        _try_send_verification(user, request)


def _try_send_verification(user, request) -> None:
    try:
        send_verification_mail(user, request)
    except security_mail.MailBudgetExceeded:
        pass  # gleiche Antwort; Benutzer kann spaeter neu anfordern


def cleanup_pending_registrations() -> int:
    User = get_user_model()
    cutoff = timezone.now() - timedelta(days=settings.PENDING_REGISTRATION_RETENTION_DAYS)
    qs = User.objects.filter(
        state=User.State.PENDING_EMAIL, email_verified_at__isnull=True,
        origin=User.Origin.SELF_REGISTRATION, date_joined__lt=cutoff, last_login__isnull=True,
    )
    deleted, _ = qs.delete()
    return deleted


# --- E-Mail-Login-Codes: Versand mit Budget und Sitzungsbindung -------------

@dataclass
class ChallengeResult:
    sent: bool
    reason: str = ""


def _nonce_hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode()).hexdigest()


def issue_challenge(request, device, user, purpose: str) -> ChallengeResult:
    """Erzeugt (falls erlaubt) eine neue Herausforderung fuer das Geraet.

    TOTP: keine Aktion. E-Mail: Cooldown, Empfaengerpruefung, Budget, dann neue
    Challenge (ersetzt alle bisherigen des Kontos) und Versand. Bei Fehler keine
    Freigabe, kein Passwort-only-Fallback.
    """
    if not isinstance(device, EmailDevice):
        device.generate_challenge()
        return ChallengeResult(sent=True)
    allowed, _data = device.generate_is_allowed()
    if not allowed:
        return ChallengeResult(sent=False, reason="cooldown")
    if device.email not in (None, "", user.email) or user.email_verified_at is None:
        logger.warning("E-Mail-Code verweigert: Empfaenger weicht von verifizierter Adresse ab")
        return ChallengeResult(sent=False, reason="recipient")
    try:
        security_mail.reserve_budget(purpose=f"otp-{purpose}", recipient=user.email, user=user, request=request)
    except (security_mail.MailBudgetExceeded, rate_limits.RateLimiterUnavailable):
        return ChallengeResult(sent=False, reason="budget")

    nonce = secrets.token_urlsafe(24)
    now = timezone.now()
    with transaction.atomic():
        LoginChallenge.objects.filter(user=user, state=LoginChallenge.State.ACTIVE).update(
            state=LoginChallenge.State.SUPERSEDED
        )
        challenge = LoginChallenge.objects.create(
            user=user, purpose=purpose, nonce_hash=_nonce_hash(nonce), created_at=now,
            expires_at=now + timedelta(seconds=settings.OTP_EMAIL_TOKEN_VALIDITY),
        )
    request.session[SESSION_CHALLENGE] = {"id": challenge.pk, "nonce": nonce, "purpose": purpose}
    try:
        device.generate_challenge()  # erzeugt neuen Code (alter wird ungueltig) und versendet
    except Exception as exc:
        logger.warning("E-Mail-Code nicht zugestellt: %s", type(exc).__name__)
        LoginChallenge.objects.filter(pk=challenge.pk).update(state=LoginChallenge.State.FAILED)
        return ChallengeResult(sent=False, reason="send_failed")
    return ChallengeResult(sent=True)


class ChallengeInvalid(Exception):
    pass


def lock_active_challenge(request, user, purpose: str) -> LoginChallenge:
    """Muss innerhalb einer Transaktion aufgerufen werden."""
    data = request.session.get(SESSION_CHALLENGE) or {}
    challenge = (
        LoginChallenge.objects.select_for_update()
        .filter(pk=data.get("id"), user=user, purpose=purpose).first()
    )
    if (
        challenge is None
        or challenge.state != LoginChallenge.State.ACTIVE
        or not constant_time_compare(challenge.nonce_hash, _nonce_hash(str(data.get("nonce", ""))))
        or challenge.expires_at <= timezone.now()
        or challenge.attempts >= settings.LOGIN_CHALLENGE_MAX_ATTEMPTS
    ):
        raise ChallengeInvalid()
    return challenge


def reserve_otp_attempt(request, user) -> None:
    """Konto- und IP-Drosselung fuer Code-Eingaben (zusaetzlich zu django-otp)."""
    rate_limits.reserve([
        *rate_limits.configured("otp_attempt_account", str(user.pk)),
        *rate_limits.configured("otp_attempt_ip", rate_limits.client_ip(request)),
    ])
