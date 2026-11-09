"""Versendet Aktivierungseinladungen an vorbereitete Initialkonten (Auftrag 11.3).

Standard: --plan. Versand nur mit --apply; in Produktion zusaetzlich --confirm-send.
Jede neue Einladung macht die vorherige ungueltig; Mailbudgets gelten. Ausgabe ohne
Adressen und ohne Links.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from locations import bootstrap


class Command(BaseCommand):
    help = "Aktivierungseinladungen planen oder versenden."

    def add_arguments(self, parser):
        parser.add_argument("--environment", required=True)
        parser.add_argument("--base-url", required=True, help="z. B. https://<host> (ohne Pfad)")
        parser.add_argument("--role", choices=sorted(bootstrap.REQUIRED_ROLES))
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--plan", action="store_true")
        mode.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm-send", action="store_true")

    def handle(self, *args, environment, base_url, role=None, apply=False, confirm_send=False, **options):
        if environment != settings.ENVIRONMENT:
            raise CommandError("--environment passt nicht zur konfigurierten Umgebung; abgebrochen.")
        if not base_url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise CommandError("--base-url muss https:// sein (lokal nur 127.0.0.1/localhost).")
        if settings.ENVIRONMENT == "production" and apply and not confirm_send:
            raise CommandError("Produktiver Versand verlangt zusaetzlich --confirm-send.")
        candidates = [r for r in bootstrap.invitation_candidates() if role is None or r.target_role == role]
        for record in candidates:
            if apply:
                sent = bootstrap.send_invitation(record.pk, base_url)
                self.stdout.write(f"  {record.target_role:15} {'gesendet' if sent else 'NICHT gesendet (Budget/Zustellung)'}")
            else:
                self.stdout.write(f"  {record.target_role:15} wuerde eingeladen")
        if not candidates:
            self.stdout.write("Keine einladbaren Initialkonten.")
