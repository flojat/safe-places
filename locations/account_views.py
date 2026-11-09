"""Konto-, Registrierungs- und Anmeldeansichten.

Login und Faktoreinrichtung bauen auf django-two-factor-auth auf und ergaenzen:
- nur aktivierte Konten mit bestaetigter E-Mail duerfen sich anmelden
- E-Mail-Codes mit Versandbudget und Bindung an die Browsersitzung
- atomarer Codeverbrauch (Geraet waehrend der Pruefung gesperrt)
- Konto-/IP-Drosselung fuer Codeeingaben
- ohne Faktor immer zuerst Einrichtung, nie Passwort-only-Mitgliedszugang
"""

from __future__ import annotations

from django import forms
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, SetPasswordForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django_otp import devices_for_user
from django_otp.plugins.otp_email.models import EmailDevice
from django_otp.plugins.otp_static.models import StaticDevice
from two_factor.forms import BackupTokenForm, DeviceValidationForm, TOTPDeviceForm
from two_factor.utils import get_otpauth_url, totp_digits
from two_factor.views import core as tf_core
from two_factor.views.utils import IdempotentSessionWizardView

from . import auth_flows, rate_limits
from .middleware import init_session
from .models import AuditEvent, normalize_email_address
from .permissions import auth_state, fully_verified_required
from .services import security_mail

GENERIC_CODE_ERROR = "Der Code ist ungültig oder abgelaufen. Bitte fordern Sie bei Bedarf einen neuen Code an."
TOO_MANY_ERROR = "Zu viele Versuche. Bitte warten Sie einige Minuten und versuchen Sie es erneut."


def _no_store(response):
    response["Cache-Control"] = "private, no-store"
    # same-origin statt no-referrer: Browser senden bei no-referrer "Origin: null",
    # was Djangos CSRF-Pruefung (zu Recht) ablehnt. Externe Ziele erhalten keinen Referrer.
    response["Referrer-Policy"] = "same-origin"
    return response


# --- Formulare ---------------------------------------------------------------

class AccountAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(label="E-Mail", widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"}))

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "E-Mail-Adresse oder Passwort ist falsch.",
        "not_activated": "Dieses Konto ist noch nicht aktiviert. Bitte bestätigen Sie zuerst Ihre E-Mail-Adresse.",
    }

    def clean_username(self):
        return normalize_email_address(self.cleaned_data.get("username", ""))

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if user.state != user.State.ACTIVE or user.email_verified_at is None:
            raise ValidationError(self.error_messages["not_activated"], code="not_activated")


class GuardedTokenMixin:
    """Drosselung, Sitzungsbindung (E-Mail) und atomarer Verbrauch fuer Codeformulare."""

    request = None
    challenge_purpose = "login"

    def _guard_user(self):
        return getattr(self, "user", None)

    def _guard_device(self):
        return getattr(self, "initial_device", None) or getattr(self, "device", None)

    def _set_guard_device(self, device):
        if hasattr(self, "initial_device"):
            self.initial_device = device
        if hasattr(self, "device"):
            self.device = device

    def _guarded_verify(self, verify):
        """Fuehrt die eigentliche Paketpruefung `verify` geschuetzt aus."""
        user = self._guard_user()
        if self.request is None or user is None:
            raise ValidationError(GENERIC_CODE_ERROR)
        try:
            auth_flows.reserve_otp_attempt(self.request, user)
        except (rate_limits.RateLimited, rate_limits.RateLimiterUnavailable):
            raise ValidationError(TOO_MANY_ERROR)
        device = self._guard_device()
        is_email = isinstance(device, EmailDevice) and not isinstance(self, BackupTokenForm)
        error = result = None
        with transaction.atomic():
            challenge = None
            if is_email:
                try:
                    challenge = auth_flows.lock_active_challenge(self.request, user, self.challenge_purpose)
                except auth_flows.ChallengeInvalid:
                    raise ValidationError(GENERIC_CODE_ERROR)
            if device is not None and device.pk:
                self._set_guard_device(type(device).objects.select_for_update().get(pk=device.pk))
            try:
                result = verify()
            except ValidationError as exc:
                error = exc
            used_backup = error is None and isinstance(self, BackupTokenForm)
            if used_backup:
                AuditEvent.objects.create(actor=user, action="backup_code_used", target_type="user",
                                          target_id=str(user.pk), detail=self.challenge_purpose)
            if challenge is not None:
                if error is None:
                    challenge.state = challenge.State.CONSUMED
                    self.request.session.pop(auth_flows.SESSION_CHALLENGE, None)
                else:
                    challenge.attempts += 1
                challenge.save(update_fields=["state", "attempts"])
        if error is not None:
            raise ValidationError(GENERIC_CODE_ERROR)
        if used_backup:  # nach der Transaktion, ohne offene DB-Sperren
            from .notifications import notify

            notify(user, "backup_code_used", self.request)
        return result

    def clean(self):
        # Loginformulare pruefen in clean(); Setupformulare in clean_token().
        if "otp_token" in self.fields:
            if self.errors:
                return self.cleaned_data
            self._guarded_verify(super().clean)
            return self.cleaned_data
        return super().clean()

    def clean_token(self):
        return self._guarded_verify(super().clean_token)


_guarded_cache: dict[type, type] = {}


def guarded(form_class: type, purpose: str) -> type:
    key = (form_class, purpose)
    if key not in _guarded_cache:
        _guarded_cache[key] = type(
            f"Guarded{form_class.__name__}", (GuardedTokenMixin, form_class), {"challenge_purpose": purpose}
        )
    return _guarded_cache[key]


class GuardedBackupTokenForm(GuardedTokenMixin, BackupTokenForm):
    pass


# --- Login und Einrichtung ---------------------------------------------------

class LoginView(tf_core.LoginView):
    form_list = (
        (tf_core.LoginView.AUTH_STEP, AccountAuthenticationForm),
        (tf_core.LoginView.TOKEN_STEP, tf_core.AuthenticationTokenForm),
        (tf_core.LoginView.BACKUP_STEP, GuardedBackupTokenForm),
    )
    template_name = "two_factor/core/login.html"

    def get_form(self, step=None, **kwargs):
        form = super().get_form(step=step, **kwargs)
        if (step or self.steps.current) in (self.TOKEN_STEP, self.BACKUP_STEP):
            if not isinstance(form, GuardedTokenMixin):
                form.__class__ = guarded(type(form), "login")
            form.request = self.request
        return form

    def render(self, form=None, **kwargs):
        if self.steps.current == self.TOKEN_STEP:
            form_with_errors = form is not None and form.is_bound and not form.is_valid()
            if not form_with_errors:
                result = auth_flows.issue_challenge(self.request, self.get_device(), self.get_user(), "login")
                kwargs["challenge_result"] = result
        return IdempotentSessionWizardView.render(self, form, **kwargs)

    def done(self, form_list, **kwargs):
        response = super().done(form_list, **kwargs)
        user = self.get_user()
        AuditEvent.objects.create(actor=user, action="login", target_type="user", target_id=str(user.pk),
                                  detail="mfa" if getattr(user, "otp_device", None) else "setup_pending")
        if not getattr(user, "otp_device", None):
            return redirect("two_factor:setup")
        from .bootstrap import finalize_roles

        finalize_roles(user)
        return response

    def get_context_data(self, form, **kwargs):
        context = super().get_context_data(form, **kwargs)
        if self.steps.current == self.TOKEN_STEP:
            context["device_kind"] = "email" if isinstance(self.get_device(), EmailDevice) else "totp"
        return context

    def get(self, request, *args, **kwargs):
        return _no_store(super().get(request, *args, **kwargs))

    def post(self, request, *args, **kwargs):
        return _no_store(super().post(request, *args, **kwargs))


ALLOWED_METHODS = {"generator", "email"}


class SetupView(tf_core.SetupView):
    """Faktoreinrichtung: Benutzer waehlt selbst E-Mail-Code oder Authenticator-App."""

    def dispatch(self, request, *args, **kwargs):
        state = auth_state(request)
        if state == "anonymous":
            return redirect("two_factor:login")
        if state == "pending_email":
            return render(request, "account/not_activated.html", status=403)
        if not getattr(self, "is_additional_factor", False) and _device_kinds(request.user):
            # Erst-Einrichtung nur ohne vorhandenen Faktor (GET und POST); weitere Faktoren
            # ausschliesslich ueber AddFactorView mit frischer Bestaetigung.
            return redirect("account:home")
        return super().dispatch(request, *args, **kwargs)

    def get_available_methods(self):
        return [m for m in super().get_available_methods() if m.code in ALLOWED_METHODS]

    METHOD_LABELS = {
        "generator": "Authenticator-App (empfohlen)",
        "email": "E-Mail-Code (einfacher, aber schwächer)",
    }

    def get_form(self, step=None, **kwargs):
        form = super().get_form(step=step, **kwargs)
        if "method" in getattr(form, "fields", {}):
            available = {m.code for m in self.get_available_methods()}
            form.fields["method"].choices = [
                (code, self.METHOD_LABELS.get(code, label)) for code, label in form.fields["method"].choices
                if code in available
            ]
        if isinstance(form, (TOTPDeviceForm, DeviceValidationForm)):
            if not isinstance(form, GuardedTokenMixin):
                form.__class__ = guarded(type(form), "setup")
            form.request = self.request
            if not hasattr(form, "user"):
                form.user = self.request.user
        return form

    def render_next_step(self, form, **kwargs):
        if self.steps.next == "validation":
            result = auth_flows.issue_challenge(self.request, self.get_device(), self.request.user, "setup")
            kwargs["challenge_succeeded"] = result.sent
            kwargs["challenge_result"] = result
            return IdempotentSessionWizardView.render_next_step(self, form, **kwargs)
        return super().render_next_step(form, **kwargs)

    def get_context_data(self, form, **kwargs):
        context = super().get_context_data(form, **kwargs)
        if self.steps.current == "generator":
            from django.conf import settings

            issuer = settings.OTP_TOTP_ISSUER
            context["issuer"] = issuer
            context["otpauth_url"] = get_otpauth_url(
                self.request.user.get_username(), context["secret_key"], issuer, totp_digits()
            )
        return context

    def done(self, form_list, **kwargs):
        from .bootstrap import finalize_roles

        response = super().done(form_list, **kwargs)
        user = self.request.user
        finalize_roles(user)  # Initialkonto: Zielrolle erst jetzt
        auth_flows.rotate_security_generation(user, self.request)
        AuditEvent.objects.create(actor=user, action="factor_added", target_type="user",
                                  target_id=str(user.pk), detail=self.get_method().code)
        if getattr(self, "is_additional_factor", False):
            from .notifications import notify

            notify(user, "factor_added", self.request)
        return response


class QRGeneratorView(tf_core.QRGeneratorView):
    def get_issuer(self):
        from django.conf import settings

        return settings.OTP_TOTP_ISSUER

    def get(self, request, *args, **kwargs):
        return _no_store(super().get(request, *args, **kwargs))


@method_decorator(fully_verified_required, name="dispatch")
class BackupTokensView(tf_core.BackupTokensView):
    def post(self, request, *args, **kwargs):
        from .reauth import is_recent, redirect_to_reauth

        if not is_recent(request):  # neue Codes = sensible Faktoraenderung
            return redirect_to_reauth(request, request.path)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        AuditEvent.objects.create(actor=self.request.user, action="backup_codes_generated",
                                  target_type="user", target_id=str(self.request.user.pk))
        return response

    def get(self, request, *args, **kwargs):
        return _no_store(super().get(request, *args, **kwargs))


@method_decorator(fully_verified_required, name="dispatch")
class SetupCompleteView(tf_core.SetupCompleteView):
    pass


# --- Registrierung und E-Mail-Bestaetigung -------------------------------------

class RegistrationForm(forms.Form):
    email = forms.EmailField(label="E-Mail", max_length=254, widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    password1 = forms.CharField(label="Passwort", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label="Passwort wiederholen", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Die Passwörter stimmen nicht überein.")
        if p1 and data.get("email"):
            from django.contrib.auth import get_user_model

            probe = get_user_model()(email=normalize_email_address(data["email"]))
            try:
                validate_password(p1, probe)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return data


@never_cache
@require_http_methods(["GET", "POST"])
def register(request):
    from django.conf import settings as dj_settings

    from .bootstrap import admin_ready

    if dj_settings.REGISTRATION_REQUIRES_ADMIN and not admin_ready():
        return _no_store(render(request, "account/registration_closed.html", status=503))
    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            try:
                auth_flows.register(email=form.cleaned_data["email"], password=form.cleaned_data["password1"],
                                    request=request)
            except auth_flows.RegistrationRejected as exc:
                response = render(request, "account/rate_limited.html", {"retry_minutes": max(1, exc.retry_after // 60)}, status=429)
                response["Retry-After"] = str(exc.retry_after)
                return _no_store(response)
            except rate_limits.RateLimiterUnavailable:
                return _no_store(render(request, "account/unavailable.html", status=503))
            return _no_store(render(request, "account/register_done.html"))
    else:
        form = RegistrationForm()
    return _no_store(render(request, "account/register.html", {"form": form}))


@never_cache
@require_http_methods(["GET"])
def verify_email_token(request, token: str):
    """Link aus der Mail: Token nur in die Sitzung uebernehmen, dann tokenfreie URL.
    GET verbraucht nichts (Mail-Scanner)."""
    request.session[auth_flows.SESSION_VERIFY_TOKEN] = token
    response = _no_store(redirect("account:verify_email"))
    response["Referrer-Policy"] = "no-referrer"  # tokenhaltige URL nie weitergeben
    return response


@never_cache
@require_http_methods(["GET", "POST"])
def verify_email(request):
    token = request.session.get(auth_flows.SESSION_VERIFY_TOKEN)
    if request.method == "POST":
        if not token:
            return _no_store(render(request, "account/verify_invalid.html", status=400))
        try:
            auth_flows.consume_verification_token(token)
        except auth_flows.VerificationError:
            request.session.pop(auth_flows.SESSION_VERIFY_TOKEN, None)
            return _no_store(render(request, "account/verify_invalid.html", status=400))
        request.session.pop(auth_flows.SESSION_VERIFY_TOKEN, None)
        return _no_store(render(request, "account/verify_done.html"))
    valid = False
    if token:
        try:
            auth_flows.check_verification_token(token)
            valid = True
        except auth_flows.VerificationError:
            valid = False
    if not valid:
        return _no_store(render(request, "account/verify_invalid.html", status=400))
    return _no_store(render(request, "account/verify_confirm.html"))


class ResendForm(forms.Form):
    email = forms.EmailField(label="E-Mail", max_length=254)


@never_cache
@require_http_methods(["GET", "POST"])
def resend_verification(request):
    if request.method == "POST":
        form = ResendForm(request.POST)
        if form.is_valid():
            try:
                auth_flows.resend_verification(email=form.cleaned_data["email"], request=request)
            except rate_limits.RateLimited as exc:
                response = render(request, "account/rate_limited.html", {"retry_minutes": max(1, exc.retry_after // 60)}, status=429)
                response["Retry-After"] = str(exc.retry_after)
                return _no_store(response)
            except rate_limits.RateLimiterUnavailable:
                return _no_store(render(request, "account/unavailable.html", status=503))
            return _no_store(render(request, "account/register_done.html", {"resent": True}))
    else:
        form = ResendForm()
    return _no_store(render(request, "account/resend.html", {"form": form}))


# --- Konto, Logout -----------------------------------------------------------

@never_cache
@login_required
def account_home(request):
    user = request.user
    devices = [
        {"type": "Authenticator-App" if d.__class__.__name__ == "TOTPDevice" else "E-Mail-Code",
         "kind": "generator" if d.__class__.__name__ == "TOTPDevice" else "email"}
        for d in devices_for_user(user) if not isinstance(d, StaticDevice)
    ]
    backup = StaticDevice.objects.filter(user=user).first()
    from .member_access import quota_status

    state = auth_state(request)
    return _no_store(render(request, "account/home.html", {
        "quota": quota_status(user) if state == "fully_verified" else None,
        "state": state,
        "devices": devices,
        "backup_count": backup.token_set.count() if backup else 0,
    }))


class LogoutView(auth_views.LogoutView):
    """Nur POST (Django). Leert zusaetzlich den Browsercache dieser Seite."""

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        response["Clear-Site-Data"] = '"cache"'
        return _no_store(response)


# --- Passwortreset -----------------------------------------------------------

class BudgetedPasswordResetForm(PasswordResetForm):
    email = forms.EmailField(label="E-Mail", max_length=254)

    def get_users(self, email):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        return (
            u for u in User.objects.filter(email=normalize_email_address(email), is_active=True,
                                           state=User.State.ACTIVE)
            if u.has_usable_password()
        )

    def save(self, *args, request=None, **kwargs):
        self._request = request
        return super().save(*args, request=request, **kwargs)

    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email,
                  html_email_template_name=None):
        subject = "".join(render_to_string(subject_template_name, context).splitlines())
        body = render_to_string(email_template_name, context)
        try:
            security_mail.send(purpose="reset", recipient=to_email, subject=subject, body=body,
                               user=context.get("user"), request=self._request)
        except (security_mail.MailBudgetExceeded, rate_limits.RateLimiterUnavailable):
            pass  # gleiche Antwort an den Browser


class PasswordResetView(auth_views.PasswordResetView):
    form_class = BudgetedPasswordResetForm
    template_name = "account/password_reset.html"
    email_template_name = "emails/password_reset.txt"
    subject_template_name = "emails/password_reset_subject.txt"
    success_url = reverse_lazy("account:password_reset_done")

    def form_valid(self, form):
        try:
            rate_limits.reserve(rate_limits.configured("reset_request_ip", rate_limits.client_ip(self.request)))
        except rate_limits.RateLimited as exc:
            response = render(self.request, "account/rate_limited.html", {"retry_minutes": max(1, exc.retry_after // 60)}, status=429)
            response["Retry-After"] = str(exc.retry_after)
            return _no_store(response)
        return super().form_valid(form)


class AccountSetPasswordForm(SetPasswordForm):
    def save(self, commit=True):
        user = super().save(commit=False)
        user.security_generation += 1  # alle Sitzungen und Login-Zustaende beenden
        if commit:
            user.save()
            AuditEvent.objects.create(actor=user, action="password_reset", target_type="user",
                                      target_id=str(user.pk))
            from .notifications import notify

            notify(user, "password_reset")
        return user


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    form_class = AccountSetPasswordForm
    template_name = "account/password_reset_confirm.html"
    success_url = reverse_lazy("account:password_reset_complete")
    post_reset_login = False  # nie automatisch anmelden


def on_user_logged_in(sender, request, user, **kwargs):
    if request is not None and hasattr(request, "session"):
        init_session(request, user)


# --- E-Mail-Aenderung ----------------------------------------------------------

class EmailChangeForm(forms.Form):
    new_email = forms.EmailField(label="Neue E-Mail-Adresse", max_length=254)


@never_cache
@require_http_methods(["GET", "POST"])
@fully_verified_required
def email_change(request):
    from .reauth import is_recent, redirect_to_reauth

    if not is_recent(request):
        return redirect_to_reauth(request, request.get_full_path())
    form = EmailChangeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            auth_flows.request_email_change(request.user, form.cleaned_data["new_email"], request)
        except rate_limits.RateLimited as exc:
            response = render(request, "account/rate_limited.html", {"retry_minutes": max(1, exc.retry_after // 60)}, status=429)
            return _no_store(response)
        except rate_limits.RateLimiterUnavailable:
            return _no_store(render(request, "account/unavailable.html", status=503))
        return _no_store(render(request, "account/email_change_sent.html"))
    return _no_store(render(request, "account/email_change.html", {"form": form}))


@never_cache
@require_http_methods(["GET"])
def email_change_token(request, token: str):
    request.session[auth_flows.SESSION_CHANGE_TOKEN] = token
    response = _no_store(redirect("account:email_change_confirm"))
    response["Referrer-Policy"] = "no-referrer"
    return response


@never_cache
@require_http_methods(["GET", "POST"])
def email_change_confirm(request):
    token = request.session.get(auth_flows.SESSION_CHANGE_TOKEN)
    if request.method == "POST":
        request.session.pop(auth_flows.SESSION_CHANGE_TOKEN, None)
        try:
            if not token:
                raise auth_flows.VerificationError("fehlt")
            auth_flows.confirm_email_change(token)
        except auth_flows.VerificationError:
            return _no_store(render(request, "account/verify_invalid.html", status=400))
        return _no_store(render(request, "account/email_change_done.html"))
    if not token:
        return _no_store(render(request, "account/verify_invalid.html", status=400))
    return _no_store(render(request, "account/email_change_confirm.html"))


# --- Aktivierung von Initialkonten (Einladung) ----------------------------------

class ActivationForm(forms.Form):
    password1 = forms.CharField(label="Neues Passwort", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label="Passwort wiederholen", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean(self):
        data = super().clean()
        if data.get("password1") and data.get("password1") != data.get("password2"):
            self.add_error("password2", "Die Passwoerter stimmen nicht ueberein.")
        elif data.get("password1"):
            try:
                validate_password(data["password1"], self.user)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return data


@never_cache
@require_http_methods(["GET"])
def activate_token(request, token: str):
    from .bootstrap import SESSION_INVITE

    request.session[SESSION_INVITE] = token
    response = _no_store(redirect("account:activate"))
    response["Referrer-Policy"] = "no-referrer"
    return response


@never_cache
@require_http_methods(["GET", "POST"])
def activate(request):
    """GET zeigt nur das Formular (Mail-Scanner verbrauchen nichts); POST aktiviert einmalig."""
    from .bootstrap import SESSION_INVITE, InvitationError, accept_invitation, check_invitation

    token = request.session.get(SESSION_INVITE)
    try:
        record = check_invitation(token) if token else None
    except InvitationError:
        record = None
    if record is None:
        request.session.pop(SESSION_INVITE, None)
        return _no_store(render(request, "account/activation_invalid.html", status=400))
    form = ActivationForm(request.POST or None, user=record.user)
    if request.method == "POST" and form.is_valid():
        try:
            accept_invitation(token, form.cleaned_data["password1"])
        except InvitationError:
            return _no_store(render(request, "account/activation_invalid.html", status=400))
        finally:
            request.session.pop(SESSION_INVITE, None)
        return _no_store(render(request, "account/activation_done.html"))
    return _no_store(render(request, "account/activation.html", {"form": form}))


# --- Zusaetzlicher Faktor / Faktor entfernen ----------------------------------

def _device_kinds(user) -> set[str]:
    from django_otp.plugins.otp_totp.models import TOTPDevice

    kinds = set()
    if TOTPDevice.objects.filter(user=user, confirmed=True).exists():
        kinds.add("generator")
    if EmailDevice.objects.filter(user=user, confirmed=True).exists():
        kinds.add("email")
    return kinds


class AddFactorView(SetupView):
    """Weiteren Faktor bewusst hinzufuegen (frische Bestaetigung noetig).

    Angeboten wird nur eine noch nicht eingerichtete Methode. Kein automatischer
    E-Mail-Fallback: der zweite Faktor entsteht nur durch diese ausdrueckliche Aktion.
    """

    is_additional_factor = True

    def dispatch(self, request, *args, **kwargs):
        from .permissions import is_fully_verified
        from .reauth import is_recent, redirect_to_reauth

        if not is_fully_verified(request):
            return redirect("two_factor:login")
        if not is_recent(request):
            return redirect_to_reauth(request, request.path)
        if not self._missing_methods(request.user):
            messages.info(request, "Alle verfuegbaren Methoden sind bereits eingerichtet.")
            return redirect("account:home")
        return super().dispatch(request, *args, **kwargs)

    def _missing_methods(self, user):
        return ALLOWED_METHODS - _device_kinds(user)

    def get(self, request, *args, **kwargs):
        # SetupView leitet bei vorhandenem Geraet weiter; hier bewusst nicht.
        return IdempotentSessionWizardView.get(self, request, *args, **kwargs)

    def get_available_methods(self):
        missing = self._missing_methods(self.request.user)
        return [m for m in super().get_available_methods() if m.code in missing]

    def get_success_url(self):
        return reverse_lazy("account:home")


@never_cache
@require_http_methods(["POST"])
@fully_verified_required
def remove_factor(request):
    """Entfernt eine Methode nur, wenn danach mindestens eine andere bleibt."""
    from django_otp.plugins.otp_totp.models import TOTPDevice

    from .notifications import notify
    from .reauth import is_recent, redirect_to_reauth

    if not is_recent(request):
        return redirect_to_reauth(request, reverse_lazy("account:home"))
    kind = request.POST.get("kind")
    kinds = _device_kinds(request.user)
    if kind not in kinds or len(kinds) < 2:
        messages.error(request, "Der letzte Anmeldeschritt kann nicht entfernt werden.")
        return redirect("account:home")
    model = TOTPDevice if kind == "generator" else EmailDevice
    model.objects.filter(user=request.user).delete()
    AuditEvent.objects.create(actor=request.user, action="factor_removed", target_type="user",
                              target_id=str(request.user.pk), detail=kind)
    auth_flows.rotate_security_generation(request.user, request)
    notify(request.user, "factor_removed", request)
    messages.success(request, "Anmeldeschritt entfernt. Bitte melden Sie sich neu an.")
    from django.contrib.auth import logout

    logout(request)
    return redirect("two_factor:login")
