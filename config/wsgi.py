"""WSGI-Einstieg fuer Apache (mod_wsgi oder Passenger, Profil durch Betreiber zu bestaetigen)."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

application = get_wsgi_application()
