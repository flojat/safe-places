"""Passenger-Startdatei (PassengerAppType wsgi, PassengerStartupFile passenger_wsgi.py).

Liegt im Release-Stamm. config/wsgi.py findet die private Konfiguration im
Release-Layout unter <app-home>/shared/config/.env.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.wsgi import application  # noqa: E402,F401
