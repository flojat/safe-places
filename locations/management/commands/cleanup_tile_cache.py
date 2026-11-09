"""Begrenzt den Grundkarten-Cache (alte Generationen, Alter, Gesamtgroesse)."""

from django.core.management.base import BaseCommand

from locations.services import swisstopo


class Command(BaseCommand):
    help = "Tile-Cache bereinigen."

    def handle(self, *args, **options):
        result = swisstopo.cleanup_cache()
        self.stdout.write(
            f"cleanup_tile_cache: {result['generations']} alte Generationen, "
            f"{result['expired']} abgelaufene, {result['evicted']} verdraengte Kacheln entfernt."
        )
