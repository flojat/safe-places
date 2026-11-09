"""Synthetische Demodaten (nur Entwicklung/Test, Auftrag 11.4).

- deterministisch und idempotent ueber external_id "demo-NNN"
- bestehende Objekte (auch manuell geaenderte) werden nie veraendert
- keine Benutzer, Passwoerter oder echten Adressen; Kontakte nur mit
  reservierten Beispieldomains
- Canary-Werte markieren Felder, die nie in Gast-/Mitgliedsausgaben erscheinen duerfen
"""

from __future__ import annotations

import random

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from locations.geo import coarse_preview_point, to_coordinate
from locations.models import Category, Location, Provider
from locations.services import publishing

REGIONS = [
    ("Zürich", 47.3769, 8.5417), ("Bern", 46.9480, 7.4474), ("Basel", 47.5596, 7.5886),
    ("Luzern", 47.0502, 8.3093), ("St. Gallen", 47.4245, 9.3767), ("Lausanne", 46.5197, 6.6323),
    ("Genève", 46.2044, 6.1432), ("Lugano", 46.0037, 8.9511), ("Chur", 46.8508, 9.5320),
    ("Sion", 46.2331, 7.3606), ("Winterthur", 47.4988, 8.7237), ("Biel/Bienne", 47.1368, 7.2468),
]
CATEGORIES = [c for c, _ in Category.choices]
SEED = 20261109

# Erkennungsmerker fuer Tests (siehe locations/tests).
CANARY_PREFIX = "CANARY-"
MEMBER_MARKER = "MITGLIEDERDETAIL-"


class Command(BaseCommand):
    help = "Erzeugt synthetische Demoobjekte (nur lokal/Test)."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=100)

    def handle(self, *args, count: int, **options):
        if settings.ENVIRONMENT == "production":
            raise CommandError("seed_demo ist in Produktion verboten.")
        if not 1 <= count <= 500:
            raise CommandError("--count muss zwischen 1 und 500 liegen.")
        rng = random.Random(SEED)
        approved_count = round(count * 0.9)
        created = skipped = published = 0

        providers = []
        for i in range(1, 11):
            provider, _ = Provider.objects.get_or_create(
                name=f"Demo-Anbieter {i:02d}", is_demo=True,
                defaults={
                    "correspondence_address": f"{CANARY_PREFIX}KORRESPONDENZ-{i:02d}",
                    "internal_notes": f"{CANARY_PREFIX}ANBIETERNOTIZ-{i:02d}",
                    "contact_name": f"{CANARY_PREFIX}ANBIETERKONTAKT-{i:02d}",
                    "contact_email": f"anbieter{i:02d}@example.net",
                },
            )
            providers.append(provider)

        for n in range(1, count + 1):
            # Zufallswerte immer ziehen, damit die Folge unabhaengig von Bestand bleibt.
            region, base_lat, base_lon = REGIONS[(n - 1) % len(REGIONS)]
            lat = to_coordinate(round(base_lat + rng.uniform(-0.04, 0.04), 6), "lat")
            lon = to_coordinate(round(base_lon + rng.uniform(-0.05, 0.05), 6), "lon")
            category = CATEGORIES[rng.randrange(len(CATEGORIES))]
            external_id = f"demo-{n:03d}"
            if Location.objects.filter(external_id=external_id).exists():
                skipped += 1
                continue

            label = dict(Category.choices)[category]
            contacts_released = n % 5 != 0
            exact_released = n % 3 == 0
            p_lat, p_lon = coarse_preview_point(lat, lon)
            with transaction.atomic():
                loc = Location.objects.create(
                    external_id=external_id,
                    provider=providers[(n - 1) % len(providers)],
                    title=f"Demo-{label} {region} {n:03d}",
                    category=category,
                    preview_text=f"Synthetischer Demo-Eintrag ({label}) in der Region {region}. Kein reales Angebot.",
                    long_description=(
                        f"{MEMBER_MARKER}{n:03d}: Ausführliche synthetische Beschreibung nur für "
                        f"vollständig angemeldete Mitglieder. Öffnungszeiten, Zugang und Ablauf sind erfunden."
                    ),
                    details=f"{MEMBER_MARKER}DETAILS-{n:03d}: Barrierefreiheit teilweise; Sprachen DE/FR/IT (Demo).",
                    exact_address=(
                        f"{MEMBER_MARKER}ADRESSE-{n:03d}, Demoweg {n}, {region}" if exact_released
                        else f"{CANARY_PREFIX}ADRESSE-INTERN-{n:03d}"
                    ),
                    exact_lat=lat, exact_lon=lon,
                    member_exact_location_released=exact_released,
                    preview_lat=p_lat, preview_lon=p_lon,
                    place_text=region,
                    contact_name=(f"Demo-Kontakt {n:03d}" if contacts_released
                                  else f"{CANARY_PREFIX}KONTAKT-NICHT-FREIGEGEBEN-{n:03d}"),
                    contact_organisation=f"Demo-Organisation {(n - 1) % 10 + 1:02d}",
                    contact_email=(f"kontakt{n:03d}@example.org" if contacts_released
                                   else f"canary{n:03d}@example.invalid"),
                    contact_phone=f"+41 00 000 {n:04d}" if contacts_released else "",
                    contact_website=f"https://example.org/demo/{n:03d}" if contacts_released else "",
                    contacts_released=contacts_released,
                    internal_notes=f"{CANARY_PREFIX}INTERN-{n:03d}",
                    editorial_notes=f"{CANARY_PREFIX}REDAKTION-{n:03d}",
                    is_demo=True,
                )
            created += 1
            if n <= approved_count:
                publishing.submit(loc.pk, None, loc.work_version)
                publishing.publish(loc.pk, None, loc.work_version)
                published += 1

        self.stdout.write(
            f"seed_demo: {created} neu, {published} freigegeben, {skipped} bereits vorhanden (unveraendert)."
        )
