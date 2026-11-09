"""Einheitlicher Benutzername fuer django-axes (Login-Wizard-Feld und Credentials)."""

from .models import normalize_email_address


def axes_username(request, credentials):
    if credentials and credentials.get("username"):
        return normalize_email_address(credentials["username"])[:254]
    if request is not None and request.method == "POST":
        return normalize_email_address(request.POST.get("auth-username", ""))[:254]
    return ""
