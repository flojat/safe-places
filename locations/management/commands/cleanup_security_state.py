"""Wartung (Abschnitt 8/10.7): abgelaufene Limitereignisse, Login-Challenges,
unbestaetigte Registrierungen und Django-Sitzungen bereinigen.

Per Hosting-Cron oder manuell aufrufen; installiert selbst keinen Cronjob.
"""

from datetime import timedelta

from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand
from django.utils import timezone

from locations import auth_flows, member_access, rate_limits
from locations.models import LoginChallenge


class Command(BaseCommand):
    help = "Bereinigt abgelaufene Sicherheitszustaende (Limits, Challenges, Registrierungen, Sitzungen)."

    def handle(self, *args, **options):
        now = timezone.now()
        events = rate_limits.cleanup(older_than_hours=48)
        challenges, _ = LoginChallenge.objects.filter(expires_at__lt=now - timedelta(hours=1)).delete()
        pending = auth_flows.cleanup_pending_registrations()
        sessions, _ = Session.objects.filter(expire_date__lt=now).delete()
        grants = member_access.cleanup_expired_grants(older_than_hours=48)
        self.stdout.write(
            f"cleanup_security_state: {events} Limitereignisse, {challenges} Challenges, "
            f"{pending} unbestaetigte Registrierungen, {sessions} Sitzungen, "
            f"{grants} abgelaufene Freigabebuchungen entfernt."
        )
