"""Test-Runner: stellt nach dem Datenbank-Setup die Startkategorien sicher.

Mit --keepdb laufen Datenmigrationen nicht erneut, und TransactionTestCases leeren
Tabellen; ohne diesen Schritt haengt das Ergebnis von der Testreihenfolge ab.
"""

from django.test.runner import DiscoverRunner


class Runner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        config = super().setup_databases(**kwargs)
        from locations.categories import ensure_default_categories

        ensure_default_categories()
        ensure_default_field_help()
        return config


def ensure_default_field_help():
    """Starttexte der Hilfe wie Migration 0014 (TransactionTestCases leeren auch diese Tabelle)."""
    import importlib

    from locations.models import FieldHelp

    defaults = importlib.import_module("locations.migrations.0014_short_description_field_help").DEFAULT_HELP
    for field, text in defaults.items():
        FieldHelp.objects.update_or_create(field=field, defaults={"text": text})
