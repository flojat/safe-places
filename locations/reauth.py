"""Frische Bestaetigung (Passwort + zweiter Schritt) fuer sensible Aktionen (Abschnitt 8).

Gueltig REAUTH_WINDOW (Standard 5 min) in der aktuellen Sitzung. Verwendet die
vorhandenen Faktoren des Kontos; bei E-Mail-Code wird ein eigener, sitzungs-
gebundener Code mit Zweck "reauth" gesendet. Kein Ersatzweg ohne Faktor.
"""

from __future__ import annotations

import time
from functools import wraps
from urllib.parse import urlencode

from django import forms
from django.conf import settings
from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django_otp import devices_for_user
from django_otp.plugins.otp_email.models import EmailDevice
from django_otp.plugins.otp_static.models import StaticDevice

from . import auth_flows, rate_limits
from .models import AuditEvent
from .permissions import fully_verified_required

SESSION_REAUTH = "reauth_at"


def is_recent(request) -> bool:
    ts = request.session.get(SESSION_REAUTH)
    return bool(ts) and time.time() - ts <= settings.REAUTH_WINDOW.total_seconds()


def clear(request) -> None:
    request.session.pop(SESSION_REAUTH, None)


def redirect_to_reauth(request, next_url: str):
    return redirect(reverse("account:reauth") + "?" + urlencode({"next": next_url}))


def recent_reauth_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_recent(request):
            return redirect_to_reauth(request, request.get_full_path())
        return view(request, *args, **kwargs)

    return wrapper


class ReauthForm(forms.Form):
    password = forms.CharField(label="Passwort", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    otp_token = forms.CharField(label="Code (App, E-Mail oder Wiederherstellungscode)", max_length=16)

    def __init__(self, *args, request, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request

    def clean(self):
        data = super().clean()
        user = self.request.user
        try:
            rate_limits.reserve(rate_limits.configured("reauth_account", str(user.pk)))
        except (rate_limits.RateLimited, rate_limits.RateLimiterUnavailable):
            raise ValidationError("Zu viele Versuche. Bitte spaeter erneut versuchen.")
        if authenticate(self.request, username=user.email, password=data.get("password", "")) != user:
            raise ValidationError("Passwort oder Code ungueltig.")
        token = (data.get("otp_token") or "").strip()
        for device in devices_for_user(user):
            with transaction.atomic():
                locked = type(device).objects.select_for_update().get(pk=device.pk)
                if isinstance(locked, EmailDevice):
                    try:
                        challenge = auth_flows.lock_active_challenge(self.request, user, "reauth")
                    except auth_flows.ChallengeInvalid:
                        continue
                    if locked.verify_token(token):
                        challenge.state = challenge.State.CONSUMED
                        challenge.save(update_fields=["state"])
                        return data
                    challenge.attempts += 1
                    challenge.save(update_fields=["attempts"])
                elif locked.verify_token(token):
                    if isinstance(locked, StaticDevice):
                        AuditEvent.objects.create(actor=user, action="backup_code_used", target_type="user",
                                                  target_id=str(user.pk), detail="reauth")
                    return data
        raise ValidationError("Passwort oder Code ungueltig.")


@never_cache
@require_http_methods(["GET", "POST"])
@fully_verified_required
def reauth_view(request):
    next_url = request.POST.get("next") or request.GET.get("next") or reverse("account:home")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()},
                                           require_https=request.is_secure()):
        next_url = reverse("account:home")
    email_device = EmailDevice.objects.filter(user=request.user, confirmed=True).first()
    sent = None
    if request.method == "POST" and "send_code" in request.POST and email_device is not None:
        sent = auth_flows.issue_challenge(request, email_device, request.user, "reauth")
        form = ReauthForm(request=request)
    elif request.method == "POST":
        form = ReauthForm(request.POST, request=request)
        if form.is_valid():
            request.session[SESSION_REAUTH] = int(time.time())
            request.session.cycle_key()
            AuditEvent.objects.create(actor=request.user, action="reauth", target_type="user",
                                      target_id=str(request.user.pk))
            return redirect(next_url)
    else:
        form = ReauthForm(request=request)
    response = render(request, "account/reauth.html", {
        "form": form, "next": next_url, "has_email": email_device is not None, "sent": sent,
    })
    response["Cache-Control"] = "private, no-store"
    return response
