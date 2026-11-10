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
        return config
