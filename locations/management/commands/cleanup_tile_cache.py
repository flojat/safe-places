"""Begrenzt den Grundkarten-Cache (alte Generationen, Alter, Gesamtgroesse) je Layer."""

from django.core.management.base import BaseCommand

from locations.services import swisstopo


class Command(BaseCommand):
    help = "Tile-Cache bereinigen."

    def handle(self, *args, **options):
        for layer in swisstopo.all_layers():
            result = swisstopo.cleanup_cache(layer)
            self.stdout.write(
                f"cleanup_tile_cache {layer}: {result['generations']} alte Generationen, "
                f"{result['expired']} abgelaufene, {result['evicted']} verdraengte Kacheln entfernt."
            )
