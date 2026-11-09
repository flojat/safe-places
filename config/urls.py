from django.contrib import admin
from django.urls import include, path
from two_factor.admin import AdminSiteOTPRequired
from two_factor.urls import urlpatterns as tf_urls

# Admin nur mit OTP-verifizierter Sitzung. Zusaetzliche Rollen-/Zustands-Policies
# folgen in Etappe 4 (OTPRequired allein reicht laut Auftrag nicht).
admin.site.__class__ = AdminSiteOTPRequired

# Bewusst kein static(MEDIA_URL, ...): private Medien nur ueber kontrollierte Views.
urlpatterns = [
    path("", include(tf_urls)),
    path("verwaltung/", admin.site.urls),
    path("", include("locations.urls")),
]
