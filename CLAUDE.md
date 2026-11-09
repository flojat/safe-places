# CLAUDE.md – Adresskarte (Prototyp V0.5)

Verbindlicher Auftrag: `Auftrag/Adresskarte_Swisstopo_V0.5.md` (vertraulich, nicht in Git).
Fortschritt und wirklich ausgefuehrte Pruefungen: `IMPLEMENTIERUNGSSTATUS.md`.

## Architektur

- Django 5.2 LTS, eine Fachanwendung `locations`, Django-Templates, wenig Vanilla-JS, Leaflet lokal.
- Nur MariaDB/MySQL (`django.db.backends.mysql`, mysqlclient). Kein SQLite, auch nicht in Tests.
- Eigenes Benutzermodell `locations.User` (E-Mail als Login, normalisiert: trim + lowercase,
  keine Alias-Kanonisierung). `createsuperuser` ist deaktiviert.
- Zweiter Anmeldeschritt fuer alle: django-two-factor-auth + django-otp (E-Mail-Code, TOTP,
  Wiederherstellungscodes). Kein "Browser merken". django-axes fuer Passwortfehlversuche.
- Settings: `config/settings/{base,local,test,production}.py`; private Werte aus `.env`
  (Format KEY=VALUE, Parser `config/envfile.py`, nie `source`).
- Private Medien/Testmails/Tile-Cache unter `var/` (nicht in Git, nie statisch ausgeliefert).

## Befehle

    .venv/bin/python manage.py check
    .venv/bin/python manage.py makemigrations --check --dry-run
    .venv/bin/python manage.py migrate
    .venv/bin/python manage.py test --settings=config.settings.test --keepdb
    .venv/bin/python manage.py seed_demo --count 100   # nur lokal/Test, idempotent
    .venv/bin/python manage.py runserver 127.0.0.1:8000

Abhaengigkeiten aendern: `requirements.in` / `requirements-dev.in` anpassen, dann
`pip-compile --generate-hashes --allow-unsafe --strip-extras` und
`pip install --require-hashes -r requirements.txt -r requirements-dev.txt`.
Playwright-Browser liegen projektlokal: `PLAYWRIGHT_BROWSERS_PATH=$PWD/.playwright-browsers`.

## Modulueberblick

- `locations/geo.py`: Koordinatenpruefung (NaN/Inf, CH-Rechteck), grobe Vorschaulage, bbox, Kachelgrenzen.
- `locations/projections.py`: GAST-/MITGLIED-Feldlisten; Besucherausgaben nur hierueber.
- `locations/services/publishing.py`: submit/publish/withdraw atomar mit Versionsvergleich.
- `locations/views.py`: Gastseite, `/api/punkte`, `/api/liste`, `/objekt/<id>/`, `/map/tiles/...`.
- Tests markieren interne Werte mit `CANARY-`, Mitgliedswerte mit `MITGLIEDERDETAIL-`.

## Sicherheitsregeln fuer die Weiterarbeit

- `.env`, `Auftrag/db.env`, Initialkonten-Manifest und private Testmails nie als Text in den
  Kontext laden; nur per Skript mit redigierter Ausgabe verarbeiten.
- Keine echten Adressen/Secrets in Code, Fixtures, Doku. Tests nur mit reservierten Beispieldomains.
- Kein sudo, keine globalen Installationen, keine Aenderung globaler DB-Einstellungen.
- `manage.py test` nur mit Test-Settings (Guard in `manage.py`); nie gegen Produktion.
- Keine Mail- oder Providerabrufe ausser gemockt in Tests; reale Smoke-Tests nur nach Freigabe.
- Nichts als getestet/implementiert markieren, was nicht ausgefuehrt wurde.
