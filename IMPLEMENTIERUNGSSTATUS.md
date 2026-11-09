# Implementierungsstatus – Adresskarte Prototyp V0.5

Stand: 2026-11-09. Legende: BESTANDEN = ausgefuehrt und erfolgreich · FEHLGESCHLAGEN ·
BLOCKIERT · OFFEN = noch nicht umgesetzt/ausgefuehrt.

## Umgebung (lokal, geprueft)

| Komponente | Stand |
|---|---|
| OS | Ubuntu 24.04.4 LTS (WSL2), x86_64 |
| Python | 3.12.3, projektlokale `.venv` |
| Datenbank | MariaDB 10.11.14 (lokal), getrennte Dev-/Test-DB und -Konten |
| Django | 5.2.19 |
| django-otp / django-two-factor-auth / django-axes | 1.7.5 / 1.18.1 / 9.0.0 |
| mysqlclient / Pillow | 2.3.0 (lokal gegen libmariadb gebaut) / 13.0.0 |
| Playwright / Chromium headless shell | 1.64.1 / 156.0.8078.4 (projektlokal) |
| Leaflet | 1.9.4 (stabil; 2.0 nur Beta), lokal, npm-Integritaet geprueft, `vendor-manifest.json` |
| Apache / mod_wsgi | lokal nicht vorhanden – Hostingprofil OFFEN |
| SMTP | nicht konfiguriert (lokal Datei-Backend, Tests In-Memory-Outbox) |

## Etappen

| Etappe | Inhalt | Status |
|---|---|---|
| 0 | Preflight/Hostingpruefung (`scripts/manage_installation.py`) | OFFEN |
| 0/1 | Projektgeruest, Lockdateien mit Hashes, Settings local/test/production, Benutzermodell, erste Migrationen | umgesetzt (siehe Pruefungen) |
| 1 | Datenmodell Objekt/Anbieter/Snapshot/Foto, drei Projektionen, Gastkarte/-liste, Leaflet lokal, seed_demo | umgesetzt; Gast-Requestlimits und Bildverarbeitung OFFEN (Etappe 3) |
| 2 | Registrierung, E-Mail-Bestaetigung, E-Mail-/TOTP-Login | OFFEN (Pakete eingebunden, Ablauf nicht umgesetzt) |
| 3 | Kontingente, Mitgliedsdetails, Bilder, Limits | OFFEN |
| 4 | Meine Objekte, Redaktion, Administration | OFFEN |
| 5 | swisstopo-Gateway, Budgets, Geokodierung | OFFEN |
| 6 | Initialkonten, Aktivierung, Installations-/Deploymentroutine | OFFEN |
| 7 | Konkurrenz-/Browsertests, Dokumentation | OFFEN |

## Tatsaechlich ausgefuehrte Pruefungen

| Datum | Pruefung | Ergebnis |
|---|---|---|
| 2026-11-09 | `pip install --require-hashes` (Laufzeit + Test), `pip check` | BESTANDEN |
| 2026-11-09 | `manage.py check` (local) | BESTANDEN, 0 Hinweise |
| 2026-11-09 | `migrate` auf Dev-DB; `makemigrations --check` | BESTANDEN |
| 2026-11-09 | `manage.py test --settings=config.settings.test --keepdb` (16 Tests, MariaDB) | BESTANDEN |
| 2026-11-09 | dito mit `--parallel 2` (Klon-DBs) | BESTANDEN |
| 2026-11-09 | Playwright Chromium headless Start | BESTANDEN |
| 2026-11-09 | `migrate` 0002 auf Dev-DB; `seed_demo --count 100` zweimal (100 neu/90 freigegeben, dann 0 neu) | BESTANDEN |
| 2026-11-09 | Testsuite 52 Tests (Grundlagen, Geo/bbox, Freigabe/Versionen, Projektionen, Canary-Freiheit der Gastantworten, Kachelweg, Admin-OTP, 3 Playwright-Browsertests) | BESTANDEN |
| 2026-11-09 | Browsertests 2x wiederholt (Stabilitaet) | BESTANDEN |
| 2026-11-09 | Browsertest fand doppelte Punkteanfrage nach Kartenziehen (Nachkorrektur durch Traegheit/Grenze) | behoben, danach BESTANDEN |
| – | `check --deploy` mit Produktionssettings | OFFEN |
| – | Apache/WSGI-End-to-End | BLOCKIERT (kein Hostingzugang, Profil unbestaetigt) |
| – | Echte SMTP-Zustellung | BLOCKIERT (keine Konfiguration/Freigabe) |

## Entscheidungen

- Etappe 1 liefert unter `/map/tiles/{z}/{x}/{y}.jpeg` nur lokal erzeugte Testkacheln
  (`MAP_TILE_SOURCE=local_test`); kein Providerabruf, kein Direkt-Fallback. Zoomgrenzen 7-18
  vorlaeufig, vor Etappe 5 gegen swisstopo-Doku pruefen.
- Gastlage: deterministische Rasterzellenmitte (0.02 x 0.03 Grad, ca. 2 km), sofern keine
  explizite Vorschaulage gesetzt ist. Keine Zufallsverschiebung.
- Freigegebener Stand = Tabelle `PublishedLocation` mit getrennten Gast- und Mitgliedsfeldern;
  `disclosure_version` steigt nur bei Aenderung von Mitgliedsfeldern/-fotos oder Reaktivierung.
- Mehr als 500 Treffer: Kartenpunkte leer plus Trefferzahl und Zoomhinweis.
- Admin vorlaeufig mit `AdminSiteOTPRequired` geschuetzt; volle Rollen-Policies in Etappe 4.

- E-Mail-Normalisierung: Leerraum entfernen, gesamte Adresse klein; keine Plus-/Punkt-Kanonisierung.
- `createsuperuser` deaktiviert; Administratoren nur ueber Initialkonten-Provisionierung (Etappe 6).
- Testdatenbank wird direkt genutzt (`TEST.NAME` = bereitgestellte Test-DB, `--keepdb`);
  Testkonto hat zusaetzlich nur Rechte auf `<testdb>_%` fuer parallele Klone.
- Lokale Mail: Datei-Backend in `var/private_mail`; kein Console-Backend.
