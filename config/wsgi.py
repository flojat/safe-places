"""WSGI-Einstieg fuer Apache (mod_wsgi oder Passenger; Profil durch Betreiber zu bestaetigen).

Im Release-Layout <app-home>/releases/<id>/ wird die private Konfiguration aus
<app-home>/shared/config/.env gelesen, sofern ADRESSKARTE_ENV_FILE nicht gesetzt ist.
"""

import os
from pathlib import Path

_base = Path(__file__).resolve().parent.parent
if "ADRESSKARTE_ENV_FILE" not in os.environ and _base.parent.name == "releases":
    os.environ["ADRESSKARTE_ENV_FILE"] = str(_base.parent.parent / "shared" / "config" / ".env")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
