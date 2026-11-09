"""Bereitet genau die vier Initialkonten aus einem privaten Manifest vor (Auftrag 11.2).

Standard: --plan (nur anzeigen). Erst --apply legt an. Ausgabe nennt nur Rollen und
Aktionen, nie Adressen oder Geheimnisse. Kein Passwort, keine Mail, keine Rollenvergabe.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from locations import bootstrap


class Command(BaseCommand):
    help = "Initialkonten aus privatem Manifest planen (--plan) oder anlegen (--apply)."

    def add_arguments(self, parser):
        parser.add_argument("--manifest", required=True)
        parser.add_argument("--environment", required=True, help="Muss der Zielumgebung entsprechen")
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--plan", action="store_true")
        mode.add_argument("--apply", action="store_true")

    def handle(self, *args, manifest, environment, apply=False, **options):
        if environment != settings.ENVIRONMENT:
            raise CommandError("--environment passt nicht zur konfigurierten Umgebung; abgebrochen.")
        try:
            entries = bootstrap.load_manifest(manifest)
        except bootstrap.ManifestError as exc:
            raise CommandError(str(exc))
        items = bootstrap.apply(entries) if apply else bootstrap.plan(entries)
        self.stdout.write(("APPLY" if apply else "PLAN") + f" ({settings.ENVIRONMENT}):")
        for item in items:
            self.stdout.write(f"  {item.role:15} {item.action}")
        if any(i.action == "conflict" for i in items):
            self.stdout.write("Konflikte: Adresse existiert bereits ohne Bootstrap-Zuordnung. Keine Rechte "
                              "vergeben; Aufloesung nur nach Identitaetspruefung durch den Betreiber.")
        if not apply:
            self.stdout.write("Nichts geaendert. Zum Anlegen erneut mit --apply aufrufen.")
