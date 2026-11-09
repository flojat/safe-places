# Adresskarte (Prototyp V0.5)

Schweizer Adresskarte mit swisstopo-Grundkarte. Gaeste sehen eine begrenzte Vorschau
freigegebener Objekte; vollstaendig angemeldete Mitglieder (E-Mail bestaetigt + zweiter
Anmeldeschritt) sehen innerhalb eines Kontingents Details und Kontakte. Objektbetreuung,
Redaktion und Administration arbeiten in getrennten, geschuetzten Bereichen.

**Prototyp mit synthetischen Demodaten. Nicht als produktionssicher freigegeben** – siehe
[IMPLEMENTIERUNGSSTATUS.md](IMPLEMENTIERUNGSSTATUS.md) und [docs/SECURITY.md](docs/SECURITY.md).

## Technik (tatsaechlich verwendete Versionen)

Python 3.12 · Django 5.2.19 LTS · MariaDB 10.11 (mysqlclient 2.3.0) · django-otp 1.7.5 ·
django-two-factor-auth 1.18.1 · django-axes 9.0.0 · Pillow 13.0.0 · Leaflet 1.9.4 (lokal) ·
Tests: Playwright 1.64.1/Chromium. Kein SQLite, kein Node-Build, keine externen CDNs.

## Lokale Einrichtung (Linux/WSL getestet; macOS/Windows nicht getestet)

1. Systemvoraussetzungen (durch Betreiber): Python 3.11+, `python3-venv`, `python3-dev`,
   `pkg-config`, `libmariadb-dev`, MariaDB-Server.
2. Zwei getrennte Datenbanken mit eigenen Konten (Entwicklung, Test; das Testkonto zusaetzlich
   mit Rechten auf `<testdb>_%` fuer parallele Testklone). utf8mb4, InnoDB.
3. Private Konfiguration: `.env.example` nach `.env` kopieren, Werte eintragen, `chmod 600 .env`.
4. Installation:

       python3 scripts/manage_installation.py preflight --environment local
       python3 scripts/manage_installation.py install --environment local --apply --migrate
       .venv/bin/pip install --require-hashes -r requirements-dev.txt      # nur fuer Tests
       PLAYWRIGHT_BROWSERS_PATH=$PWD/.playwright-browsers .venv/bin/playwright install chromium-headless-shell

5. Demodaten (nur lokal/Test): `.venv/bin/python manage.py seed_demo --count 100`
6. Start (nur Loopback): `.venv/bin/python manage.py runserver 127.0.0.1:8000`
7. Tests: `.venv/bin/python manage.py test --settings=config.settings.test --keepdb`

Windows PowerShell: `py -3.12 scripts\manage_installation.py ...`, Interpreter
`.venv\Scripts\python.exe` (nicht getestet).

## Erste Ablaeufe

- **Gast**: Startseite – Karte und Liste synchron, Suche/Kategorie, Vorschauseiten.
- **Selbstregistrierung**: `/konto/registrieren/` mit synthetischer Adresse; die Bestaetigungsmail
  liegt lokal in `var/private_mail/` (nur fuer Sie lesbar, keine echte Zustellung). Link oeffnen,
  bestaetigen, anmelden, zweiten Schritt waehlen (Authenticator-App empfohlen, E-Mail-Code moeglich).
- **Mitglied**: Objekt oeffnen -> "Details und Kontakte anzeigen" (zaehlt zum Kontingent).
- **Initialkonten/Administrator**: [docs/INITIAL_ACCOUNTS.md](docs/INITIAL_ACCOUNTS.md) –
  `provision_initial_accounts` und `send_initial_invitations`; kein Standardpasswort, kein
  `createsuperuser`. Nach Aktivierung und zweitem Schritt erhaelt der Administrator seine Rolle.
- **Rollen und Redaktion**: Administration vergibt Rollen unter "Verwaltung" (frische Bestaetigung
  noetig); Objektbetreuung arbeitet unter "Meine Objekte"; Redaktion gibt im Fassungsvergleich frei.
- **Karte**: `MAP_TILE_SOURCE=local_test` (Testkacheln) oder `swisstopo` (budgetiertes Gateway).

## E-Mail-Schutz und Grenzen

Passwort + E-Mail-Code ist eine zweistufige Anmeldung, aber **kein unabhaengiger zweiter Faktor**
(NIST SP 800-63B-4 schliesst E-Mail als Out-of-Band-Authentifikator aus): Wer das Postfach
kontrolliert, kann Passwortreset und Code erhalten. Fuer Redaktion und Administration wird die
Authenticator-App empfohlen; auch sie ist nicht phishingresistent.
Vor einer Veroeffentlichung ist ein realer SMTP-Dienst mit TLS noetig (Zustellbarkeit, SPF/DKIM,
Providerlimits). Typische Fehler: Spam-Ordner, Greylisting, abgelehnte Absenderdomain,
Providerlimit; Codes laufen nach 5 Minuten ab, neue Codes fruehestens nach 60 Sekunden.

## Dokumentation

[ROLES](docs/ROLES.md) · [AUTHENTICATION](docs/AUTHENTICATION.md) · [DATA_DISCLOSURE](docs/DATA_DISCLOSURE.md) ·
[ABUSE_PROTECTION](docs/ABUSE_PROTECTION.md) · [SECURITY](docs/SECURITY.md) · [DEPLOYMENT](docs/DEPLOYMENT.md) ·
[INSTALLATION](docs/INSTALLATION.md) · [INITIAL_ACCOUNTS](docs/INITIAL_ACCOUNTS.md) ·
[DEPENDENCIES](docs/DEPENDENCIES.md) · [HOSTING_CHECK](docs/HOSTING_CHECK.md) · [BACKUP](docs/BACKUP.md) ·
[SOURCES](docs/SOURCES.md) · [IMPLEMENTIERUNGSSTATUS](IMPLEMENTIERUNGSSTATUS.md)
