"""Zugriff auf die gepflegten Kategorien (Model Category).

Eine kleine Tabelle; je Aufruf eine Abfrage, kein prozessweiter Cache (mehrere
Passenger-Prozesse wuerden sonst veraltete Werte zeigen).
"""

from __future__ import annotations

from .models import Category

DEFAULT_CATEGORIES = [
    # (Kennung, Bezeichnung, Symbol, Reihenfolge) - Startbestand, danach durch die Administration gepflegt
    ("beratung", "Beratung", "sprechblase", 10),
    ("treffpunkt", "Treffpunkt", "personen", 20),
    ("unterkunft", "Unterkunft", "haus", 30),
    ("gesundheit", "Gesundheit", "herz", 40),
    ("bildung", "Bildung", "buch", 50),
    ("freizeit", "Freizeit", "sonne", 60),
]


def ensure_default_categories() -> None:
    """Legt fehlende Startkategorien an (idempotent; aendert bestehende nicht)."""
    for slug, label, icon, order in DEFAULT_CATEGORIES:
        Category.objects.get_or_create(slug=slug, defaults={"label": label, "icon": icon, "sort_order": order})


def active() -> list[Category]:
    return list(Category.objects.filter(is_active=True))


def choices() -> list[tuple[str, str]]:
    return [(c.slug, c.label) for c in active()]


def lookup() -> dict[str, Category]:
    """Aktive Kategorien nach Kennung, in gepflegter Reihenfolge."""
    return {c.slug: c for c in active()}


def items(values, table: dict[str, Category] | None = None) -> list[dict]:
    """Zugeordnete aktive Kategorien als Wert, Bezeichnung und Symbol (gepflegte Reihenfolge)."""
    table = lookup() if table is None else table
    chosen = set(values or [])
    return [{"value": slug, "label": c.label, "icon": c.icon} for slug, c in table.items() if slug in chosen]
