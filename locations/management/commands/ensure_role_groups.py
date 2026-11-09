"""Legt die Rollengruppen (Objektbetreuung, Redaktion) samt Rechten idempotent an.

Ausdruecklicher Betreiberbefehl; vergibt keinem Konto eine Rolle.
"""

from django.core.management.base import BaseCommand

from locations.roles import ensure_role_groups


class Command(BaseCommand):
    help = "Rollengruppen und deren Rechte anlegen/aktualisieren (ohne Kontozuweisung)."

    def handle(self, *args, **options):
        groups = ensure_role_groups()
        self.stdout.write("Rollengruppen: " + ", ".join(sorted(groups)))
