from django.apps import AppConfig


class LocationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "locations"
    verbose_name = "Adresskarte"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in

        from .account_views import on_user_logged_in

        user_logged_in.connect(on_user_logged_in, dispatch_uid="locations.init_session")
