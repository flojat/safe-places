from django.contrib import admin
from django.urls import include, path

from locations import account_views as av
from locations import geocode_views
from locations.admin_site import SecureAdminSite

# Admin nur mit vollstaendig verifizierter Sitzung (Passwort + zweiter Schritt).
admin.site.__class__ = SecureAdminSite

# two_factor-Namensraum mit eigenen, abgesicherten Views. Bewusst NICHT
# eingebunden: two_factor "profile" und "disable" (letzter Faktor waere
# ersatzlos loeschbar) sowie Telefonmethoden.
two_factor_patterns = ([
    path("anmelden/", av.LoginView.as_view(), name="login"),
    path("zweiter-schritt/einrichten/", av.SetupView.as_view(), name="setup"),
    path("zweiter-schritt/qr/", av.QRGeneratorView.as_view(), name="qr"),
    path("zweiter-schritt/fertig/", av.SetupCompleteView.as_view(), name="setup_complete"),
    path("zweiter-schritt/wiederherstellungscodes/", av.BackupTokensView.as_view(), name="backup_tokens"),
], "two_factor")

# Bewusst kein static(MEDIA_URL, ...): private Medien nur ueber kontrollierte Views.
urlpatterns = [
    path("konto/", include(two_factor_patterns)),
    path("konto/", include("locations.account_urls")),
    path("meine-objekte/", include("locations.portal_urls")),
    path("verwaltung/adresssuche/", geocode_views.admin_geocode, name="admin_geocode"),
    path("verwaltung/", admin.site.urls),
    path("", include("locations.urls")),
]
