"""Prueft den swisstopo-cacheUpdate-Stand aller Grundkarten (Karte, Luftbild; je 1 REST-Abruf, budgetiert).

Per Hosting-Cron z. B. alle 6 Stunden. Bei Aenderung wird eine neue Cachegeneration
verwendet; alte Kacheln entfernt `cleanup_tile_cache`. Ein Fehler bei einem Layer
verhindert die Pruefung der anderen nicht.
"""

from django.core.management.base import BaseCommand, CommandError

from locations.services import swisstopo


class Command(BaseCommand):
    help = "swisstopo cacheUpdate pruefen und Tile-Cache bei Aenderung invalidieren."

    def handle(self, *args, **options):
        failed = []
        for layer in swisstopo.all_layers():
            try:
                changed = swisstopo.check_cache_update(layer)
            except swisstopo.ProviderUnavailable as exc:
                failed.append(f"{layer} ({exc.reason})")
                continue
            self.stdout.write(f"{layer}: " + ("Cache invalidiert (neue Generation)." if changed else "Cache aktuell."))
        if failed:
            raise CommandError("cacheUpdate nicht pruefbar: " + ", ".join(failed) + "; Cache bleibt bis Ablauf gueltig.")
