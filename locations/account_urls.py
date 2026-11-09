from django.contrib.auth import views as auth_views
from django.urls import path

from . import account_views as v
from . import reauth

app_name = "account"

urlpatterns = [
    path("", v.account_home, name="home"),
    path("registrieren/", v.register, name="register"),
    path("email-bestaetigen/", v.verify_email, name="verify_email"),
    path("email-bestaetigen/neu/", v.resend_verification, name="resend_verification"),
    path("email-bestaetigen/<str:token>/", v.verify_email_token, name="verify_email_token"),
    path("bestaetigen/", reauth.reauth_view, name="reauth"),
    path("email-aendern/", v.email_change, name="email_change"),
    path("email-aendern/bestaetigen/", v.email_change_confirm, name="email_change_confirm"),
    path("email-aendern/<str:token>/", v.email_change_token, name="email_change_token"),
    path("aktivieren/", v.activate, name="activate"),
    path("aktivieren/<str:token>/", v.activate_token, name="activate_token"),
    path("faktor-entfernen/", v.remove_factor, name="remove_factor"),
    path("abmelden/", v.LogoutView.as_view(), name="logout"),
    path("passwort-vergessen/", v.PasswordResetView.as_view(), name="password_reset"),
    path("passwort-vergessen/gesendet/",
         auth_views.PasswordResetDoneView.as_view(template_name="account/password_reset_done.html"),
         name="password_reset_done"),
    path("passwort-neu/<uidb64>/<token>/", v.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("passwort-neu/fertig/",
         auth_views.PasswordResetCompleteView.as_view(template_name="account/password_reset_complete.html"),
         name="password_reset_complete"),
]
