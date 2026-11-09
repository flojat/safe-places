# Hosting-Pruefung

Stand 2026-11-09: Zielhosting teilweise ueber Angaben des Betreibers geprueft (kein direkter Zugriff).

## Befunde Zielserver (vom Betreiber ausgefuehrt, 2026-11-09)

- Debian 12, ISPConfig, Shell-Benutzer **ohne** Jail (keine Isolation per Chroot; Schutz nur ueber Benutzer-/Dateirechte).
- Python 3.11.2 mit funktionierendem venv (pip 23.0.1); git 2.39; gcc vorhanden; libmariadb3 10.11.19.
  Fuer den Bau von mysqlclient fehlen noch `python3.11-dev`, `libmariadb-dev`, `pkg-config` (oder vorgebautes Wheel).
- Apache mit **Phusion Passenger** (laeuft) und **mod_python** (von ISPConfig).
- **Vorfall**: Installation von `libapache2-mod-wsgi-py3` loeste den automatischen Apache-Neustart des Pakets aus;
  Apache startete nicht mehr (`mod_python ... can not be used in conjunction with mod_wsgi 4.0+`), alle Websites
  kurz offline. Behoben durch `a2dismod wsgi` und Neustart. Lehre: Apache-Modulpakete unter Debian starten Apache
  bei der Installation sofort neu; vorher `apachectl -M` auf Konflikte pruefen und Wartungsfenster einplanen.
- Passenger 6.0.17 (Debian-Paket) aktiv; bisher von keiner Website genutzt. Gewaehltes Profil: **Passenger**
  (`passenger_wsgi.py`, Vorlage `deployment/apache/adresskarte-passenger-ispconfig.directives.example`),
  App-Neustart per `shared/tmp/restart.txt` (PassengerRestartDir). Betreiberbestaetigung ausstehend.
  mod_wsgi wird nicht verwendet (Paket entfernen).

| Faehigkeit | Status | Nachweis |
|---|---|---|
| Python-Webanbindung (mod_wsgi oder Passenger) | Passenger vorhanden, Website-Konfiguration ausstehend | Hoster bestaetigt Profil + passendes Python; `hosting.local.json` |
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
