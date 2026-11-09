# Installation und Pruefroutine

`scripts/manage_installation.py` nutzt nur die Python-Standardbibliothek und laeuft daher vor
der Installation der Abhaengigkeiten. Danach delegiert es an die projekteigene `.venv`.

| Modus | Zweck | Schreib-/Netzverhalten |
|---|---|---|
| `preflight` | Python, venv, Werkzeuge, Clientbibliothek, private Konfiguration, WSGI-Profil, SMTP/Cron-Status | nur lesend; Netz nur mit `--network-check` (HTTPS zu PyPI) |
| `install` | venv, `pip install --require-hashes`, `pip check`, Vendor-Hashes, `manage.py check`, `collectstatic`, Migrationsplan | `--plan` (Standard) aendert nichts; `--apply` fuehrt aus; Migration nur zusaetzlich mit `--migrate` (Produktion: `--backup-confirmed`) |
| `doctor` | Paketintegritaet gegen Lockdatei, Vendor-Hashes, `check` (Produktion `--deploy`), offene Migrationen, DEBUG/Hosts, Bootstrap-Status, Rechte | nur lesend, keine Reparatur/Seeds/Rollen |
| `deploy` | freigegebenen Git-Ref als Release vorbereiten und kontrolliert aktivieren | `--plan` pinnt den Commit; `--apply` mit exklusivem Lock, prueft Ref erneut (TOCTOU) |
| `rollback` | Code-Rueckwechsel auf ein vorhandenes Release | nur bei kompatiblem Migrationsstand; nie DB-Restore |

Exitcodes: `0` PASS/WARN · `1` FAIL · `2` BLOCKED/UNKNOWN · `3` PREPARED_NOT_ACTIVATED · `64` Bedienfehler.
Berichte (`--report <pfad>`, Rechte 0600) enthalten nur Codes, Status und generische Meldungen –
keine Secrets, Hostnamen, DB-/Kontonamen oder Konfigurationswerte.

## Lokal (Linux/WSL, getestet)

    python3 scripts/manage_installation.py preflight --environment local
    python3 scripts/manage_installation.py install --environment local --plan
    python3 scripts/manage_installation.py install --environment local --apply --migrate
    python3 scripts/manage_installation.py doctor --environment local

Testabhaengigkeiten (nur Entwicklung): `.venv/bin/pip install --require-hashes -r requirements-dev.txt`.
macOS/Windows PowerShell: gleiche Befehle mit `python` bzw. `.venv\Scripts\python.exe`;
**nicht getestet**.

## Hosteranforderungen (vor Produktion zu bestaetigen)

- Gepflegtes Python >= 3.11 (getestet 3.12) mit venv; dazu passendes mod_wsgi **oder** Passenger.
- MariaDB (getestet 10.11) mit eigener DB, eigenem Konto, utf8mb4/InnoDB; Clientbibliothek
  (libmariadb) bzw. Compiler/Header/pkg-config, falls kein passendes mysqlclient-Wheel existiert.
- Shell- oder gleichwertiger Zugang, Git (lesend), private Verzeichnisse ausserhalb des DocumentRoot.
- Ausgehendes HTTPS zu PyPI (oder Wheelhouse) und zu `wmts.geo.admin.ch`/`api3.geo.admin.ch`.
- SMTP mit TLS, zeitgesteuerte Wartung (siehe `deployment/cron.example`).

Fehlende Faehigkeiten meldet `preflight` als `FAIL`/`BLOCKED`/`UNKNOWN`, nie als bestanden.
Der Installer installiert nichts global, nutzt kein sudo und aendert keine Systemdienste.
