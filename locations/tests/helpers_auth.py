"""Hilfen fuer echte Registrierungs-/Login-Ablaeufe in Tests (keine kuenstlichen MFA-Sitzungen)."""

import base64
import re
import secrets

from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import reverse
from django_otp.oath import totp

LOGIN = "login_view"
SETUP = "setup_view"


def random_password() -> str:
    return "Tp-" + secrets.token_urlsafe(18)


def last_code() -> str:
    body = mail.outbox[-1].body
    return re.search(r"^\s*(\d{6})\s*$", body, re.M).group(1)


def last_link() -> str:
    return re.search(r"https?://\S+", mail.outbox[-1].body).group(0)


def register(client, email, password):
    return client.post(reverse("account:register"),
                       {"email": email, "password1": password, "password2": password})


def verify_from_mail(client):
    link = last_link()
    path = link.split("://", 1)[1].split("/", 1)[1]
    client.get("/" + path)
    return client.post(reverse("account:verify_email"))


def make_active_user(email, password):
    """Aktives, E-Mail-bestaetigtes Konto ohne Faktor (wie nach Verifikation)."""
    from django.utils import timezone

    User = get_user_model()
    return User.objects.create_user(email, password, state=User.State.ACTIVE, email_verified_at=timezone.now())


def login_password(client, email, password, next_url=None):
    url = reverse("two_factor:login") + (f"?next={next_url}" if next_url else "")
    return client.post(url, {f"{LOGIN}-current_step": "auth", "auth-username": email, "auth-password": password})


def login_token(client, code, next_url=None):
    url = reverse("two_factor:login") + (f"?next={next_url}" if next_url else "")
    return client.post(url, {f"{LOGIN}-current_step": "token", "token-otp_token": code})


def setup_post(client, step, **fields):
    data = {f"{SETUP}-current_step": step}
    data.update({f"{step}-{k}": v for k, v in fields.items()})
    return client.post(reverse("two_factor:setup"), data)


def setup_email(client):
    client.get(reverse("two_factor:setup"))
    setup_post(client, "welcome")
    setup_post(client, "method", method="email")  # sendet Code
    return setup_post(client, "validation", token=last_code())


def setup_totp(client):
    client.get(reverse("two_factor:setup"))
    setup_post(client, "welcome")
    setup_post(client, "method", method="generator")
    b32 = client.session["django_two_factor-qr_secret_key"]
    key = base64.b32decode(b32)
    response = setup_post(client, "generator", token=f"{totp(key):06d}")
    return response, key


def totp_code(key: bytes, offset_steps: int = 0) -> str:
    return f"{totp(key, t0=-30 * offset_steps):06d}"


def is_fully_verified(client) -> bool:
    from locations.permissions import FULLY_VERIFIED

    r = client.get(reverse("account:home"))
    return r.status_code == 200 and r.context and r.context["state"] == FULLY_VERIFIED
