from django.contrib import admin
from django.urls import include, path
from two_factor.urls import urlpatterns as tf_urls

# Bewusst kein static(MEDIA_URL, ...): private Medien nur ueber kontrollierte Views.
urlpatterns = [
    path("", include(tf_urls)),
    path("verwaltung/", admin.site.urls),
    path("", include("locations.urls")),
]
