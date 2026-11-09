from django.urls import path

from . import owner_views as v

app_name = "portal"

urlpatterns = [
    path("", v.portal_list, name="list"),
    path("neu/", v.portal_create, name="create"),
    path("<str:public_id>/", v.portal_edit, name="edit"),
    path("<str:public_id>/vorschau/", v.portal_preview, name="preview"),
    path("<str:public_id>/einreichen/", v.portal_submit, name="submit"),
    path("<str:public_id>/zurueckziehen/", v.portal_withdraw, name="withdraw"),
    path("<str:public_id>/kontakte-zurueckziehen/", v.portal_withdraw_contacts, name="withdraw_contacts"),
    path("<str:public_id>/fotos/", v.portal_photo_upload, name="photo_upload"),
    path("<str:public_id>/fotos/<str:photo_id>/", v.portal_photo_update, name="photo_update"),
]
