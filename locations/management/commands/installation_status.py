"""Lesender Kurzstatus fuer `manage_installation.py doctor` (JSON, ohne Werte/Adressen)."""

import json

from django.conf import settings
from django.core.management.base import BaseCommand

from locations import bootstrap


class Command(BaseCommand):
    help = "Lesender Installationsstatus als JSON (keine Secrets, keine Adressen)."

    def handle(self, *args, **options):
        self.stdout.write(json.dumps({
            "debug": bool(settings.DEBUG),
            "wildcard_hosts": "*" in settings.ALLOWED_HOSTS,
            "admin_ready": bootstrap.admin_ready(),
            "initial_accounts": bootstrap.status_report(),
            "tile_source": settings.MAP_TILE_SOURCE,
        }))
