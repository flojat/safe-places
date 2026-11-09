"""Parser fuer die private Umgebungsdatei.

Dokumentiertes Format (siehe .env.example):
- eine Zuweisung pro Zeile: KEY=VALUE
- KEY besteht aus Grossbuchstaben, Ziffern und Unterstrich
- Leerzeilen und Zeilen, die mit # beginnen, werden ignoriert
- der Wert ist alles nach dem ersten '=', ohne Anfuehrungszeichen-, Escape-
  oder Variablenauswertung; die Datei wird nie als Shellcode ausgefuehrt

Bereits gesetzte Prozess-Umgebungsvariablen haben Vorrang.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")


class EnvFileError(ValueError):
    pass


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    text = path.read_text(encoding="utf-8")
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            raise EnvFileError(f"{path.name}:{lineno}: keine KEY=VALUE-Zeile")
        key, value = line.split("=", 1)
        key = key.strip()
        if not _KEY.match(key):
            raise EnvFileError(f"{path.name}:{lineno}: ungueltiger Schluessel")
        if key in values:
            raise EnvFileError(f"{path.name}:{lineno}: Schluessel doppelt")
        values[key] = value
    return values


class Env:
    """Lesezugriff auf Prozessumgebung mit Fallback auf die private Datei."""

    def __init__(self, path: Path | None):
        self._file: dict[str, str] = {}
        if path is not None and path.is_file():
            self._file = parse_env_file(path)

    def get(self, key: str, default: str | None = None) -> str | None:
        if key in os.environ:
            return os.environ[key]
        return self._file.get(key, default)

    def require(self, key: str) -> str:
        value = self.get(key)
        if value is None or value == "":
            # Nur den Schluesselnamen nennen, niemals Werte.
            raise EnvFileError(f"Pflichtwert fehlt: {key}")
        return value

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.get(key)
        if value is None or value == "":
            return default
        if value.lower() in {"1", "true", "yes", "on"}:
            return True
        if value.lower() in {"0", "false", "no", "off"}:
            return False
        raise EnvFileError(f"Kein Wahrheitswert: {key}")

    def get_int(self, key: str, default: int) -> int:
        value = self.get(key)
        if value is None or value == "":
            return default
        try:
            return int(value)
        except ValueError as exc:
            raise EnvFileError(f"Keine ganze Zahl: {key}") from exc
