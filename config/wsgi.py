"""WSGI-Einstieg fuer Apache (mod_wsgi oder Passenger; Profil durch Betreiber zu bestaetigen).

Im Release-Layout <app-home>/releases/<id>/ wird die private Konfiguration aus
<app-home>/shared/config/.env gelesen, sofern ADRESSKARTE_ENV_FILE nicht gesetzt ist.
"""

import os

# Die private Konfiguration findet config/envfile.default_env_file (auch im Release-Layout).
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
