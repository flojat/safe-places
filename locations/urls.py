from django.urls import path

from . import views

app_name = "locations"

urlpatterns = [
    path("", views.home, name="home"),
]
