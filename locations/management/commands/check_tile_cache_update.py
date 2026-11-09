"""Prueft den swisstopo-cacheUpdate-Stand des Grundkarten-Layers (1 REST-Abruf, budgetiert).

Per Hosting-Cron z. B. alle 6 Stunden. Bei Aenderung wird eine neue Cachegeneration
verwendet; alte Kacheln entfernt `cleanup_tile_cache`.
"""

from django.core.management.base import BaseCommand, CommandError

from locations.services import swisstopo


class Command(BaseCommand):
    help = "swisstopo cacheUpdate pruefen und Tile-Cache bei Aenderung invalidieren."

    def handle(self, *args, **options):
        try:
            changed = swisstopo.check_cache_update()
        except swisstopo.ProviderUnavailable as exc:
            raise CommandError(f"cacheUpdate nicht pruefbar ({exc.reason}); Cache bleibt bis Ablauf gueltig.")
        self.stdout.write("Cache invalidiert (neue Generation)." if changed else "Cache aktuell.")
