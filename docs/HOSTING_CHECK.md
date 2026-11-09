# Hosting-Pruefung

Stand 2026-11-09: **Zielhosting nicht kontaktiert und nicht geprueft.** Alle hostingbezogenen
Punkte sind offen.

| Faehigkeit | Status | Nachweis |
|---|---|---|
| Python-Webanbindung (mod_wsgi oder Passenger) | BLOCKED_HOSTING_WSGI | Hoster bestaetigt Profil + passendes Python; `hosting.local.json` |
| App-Reload ohne Server-Neustart | offen | Hoster nennt erlaubten Weg (touch/Befehl); sonst `PREPARED_NOT_ACTIVATED` |
| Shell/Git (lesend, Deploy-Key) | offen | `preflight --environment production` |
| MariaDB-Version, eigenes Konto | offen | `doctor` nach Installation |
| mysqlclient (Wheel oder Build) | offen | `install --apply` auf dem Ziel |
| Private Verzeichnisse ausserhalb DocumentRoot | offen | Verzeichnisplan mit Hoster |
| Ausgehendes HTTPS (PyPI, swisstopo) | offen | `preflight --network-check` |
| SMTP mit TLS | SMTP_NOT_VERIFIED | separat freigegebener Testversand |
| Zeitgesteuerte Wartung | offen | `deployment/cron.example` |

## Was lokal geprueft wurde

- `preflight`, `install --plan`, `doctor` auf Ubuntu 24.04/WSL2, Python 3.12, MariaDB 10.11.
- Echter `deploy --plan/--apply` in ein temporaeres app-home: Git-Export, neue venv,
  Hash-Installation, Assets, Checks, `collectstatic`, Symlinkwechsel, simulierter Reload.
- **Nicht geprueft**: echte Apache-/WSGI-Ausfuehrung, Prozess-/Releasebindung, HTTPS, SMTP.

## Nach dem ersten Deployment pruefen

1. Autorisierter Webrequest liefert die Startseite aus dem neuen Release (Prozess neu gestartet).
2. `/static/...` erreichbar; `/var/`, `/shared/`, `/.env`, `/.git/`, `/releases/`, `/media/` **nicht** erreichbar.
3. Geschuetzte Fotos nur ueber `/medien/...` mit den Policies.
4. Zugriffslog enthaelt keine Token-URLs.
5. `doctor --environment production` ohne FAIL.
