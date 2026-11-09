#!/usr/bin/env python
"""Django-Verwaltung. Standard: lokale Entwicklungssettings."""

import os
import sys

# Destruktive oder testbezogene Befehle nur mit passenden Settings.
_TEST_ONLY = {"test"}
_NEVER_IN_PRODUCTION = {"test", "flush", "seed_demo", "sqlflush"}


def _guard(argv: list[str]) -> None:
    if len(argv) < 2:
        return
    command = argv[1]
    settings_arg = next(
        (a.split("=", 1)[1] for a in argv if a.startswith("--settings=")), None
    )
    module = settings_arg or os.environ.get("DJANGO_SETTINGS_MODULE", "")
    if command in _TEST_ONLY and module != "config.settings.test":
        sys.exit("Abgebrochen: 'test' nur mit --settings=config.settings.test.")
    if command in _NEVER_IN_PRODUCTION and module.endswith(".production"):
        sys.exit(f"Abgebrochen: '{command}' ist in Produktion verboten.")


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    _guard(sys.argv)
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
