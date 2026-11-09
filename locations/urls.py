from django.urls import path

from . import views

app_name = "locations"

urlpatterns = [
    path("", views.home, name="home"),
    path("api/punkte", views.api_points, name="api_points"),
    path("api/liste", views.api_list, name="api_list"),
    path("objekt/<str:public_id>/", views.preview, name="preview"),
    path("map/tiles/<int:z>/<int:x>/<int:y>.jpeg", views.tile, name="tile"),
]
