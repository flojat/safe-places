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
| 2 | Registrierung, E-Mail-Bestaetigung, E-Mail-/TOTP-Login, Sitzungsschutz, Passwortreset, atomarer Limiter | umgesetzt; offen: Faktorwechsel/E-Mail-Aenderung mit frischer Reauth, Benachrichtigungsmails, Recovery-Code-Nutzungsprotokoll (Etappe 4/6) |
| 3 | Freigabebuchungen/Kontingente, Mitgliedsdetails mit Kontakten, sichere Bildverarbeitung, Fotoauslieferung, Gast-/Mitglieds-Requestlimits | umgesetzt; Upload-UI folgt mit Etappe 4 (Portal) |
| 4 | Betreuerportal, Redaktion (Fassungsvergleich/Freigabe/Zurueckweisung), Administration (Rollen/Sperren), frische Bestaetigung, E-Mail-Aenderung | umgesetzt; offen: Benachrichtigung bei Faktoraenderung/Recovery-Code-Nutzung per Mail, zusaetzlichen Faktor nachtraeglich einrichten, administrativer Recovery-Prozess (Etappe 6/7) |
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
| 2026-11-09 | Etappe 2: Testsuite 96 Tests (u. a. E2E Registrierung->Verifikation->Passwort+E-Mail-Code und Passwort+TOTP inkl. Einrichtung; Codewiederverwendung, 5 Fehlversuche, Cooldown, parallele Challenges, SMTP-Ausfall ohne Fallback, Mailbudget, Admin nur mit zweitem Schritt, Reset ohne Login und ohne TOTP-Verlust, Axes-Sperre, Sitzungsablauf/-entzug, offene Redirects, CSRF, parallele Limiter-Reservierung und paralleler TOTP-Verbrauch mit echten DB-Verbindungen), 2x | BESTANDEN |
| 2026-11-09 | Browsertest Registrierung->Bestaetigung->Login->E-Mail-Code-Einrichtung (Chromium) | BESTANDEN |
| 2026-11-09 | Gefundene/behobene Fehler: Axes zaehlte Fehlversuche ohne Benutzernamen (Sperre griff nicht); Limiter-Deadlocks bei parallelem Bucket-Anlegen; `Referrer-Policy: no-referrer` fuehrte in Browsern zu `Origin: null` und CSRF-Ablehnung; Axes protokollierte E-Mail-Adressen | behoben, Tests BESTANDEN |
| 2026-11-09 | `seed_demo --rebuild-demo` auf Dev-DB: 100 Objekte, 100 lokal erzeugte Demobilder (25 Gast, 50 Mitglied, 25 intern) | BESTANDEN |
| 2026-11-09 | Etappe 3: Testsuite 127 Tests (u. a. GET bucht nie; Details/Kontakte nur nach POST-Buchung; Wiederabruf ohne neue Zaehlung; Kontingent greift vor sensibler Ausgabe; Kontingent ueber alle Sitzungen eines Kontos; neue disclosure_version braucht neue Buchung; Rueckzug/Logout stoppen Auslieferung trotz Buchung; Limiter-Ausfall 503; 11 parallele Oeffnungen mit echten DB-Verbindungen: genau 5 Buchungen, keine Doppelbuchung; Fotorechte Gast/Mitglied/intern/Entwurf/zurueckgezogen, auch DEBUG; Bildpruefung inkl. EXIF/GPS-Entfernung, Ausrichtung, GIF/Animation/SVG/defekt/Uebergroesse/Dekompressionsbombe/Pfadmanipulation; Gastlimit 12/min trotz gefaelschter Forwarded-Header; Login/Registrierung bei erschoepftem Limit bedienbar; Sitzungsneuanlage IP-begrenzt) | BESTANDEN |
| 2026-11-09 | Browsertest Mitglied: TOTP-Login im Browser, Rueckkehr zum Objekt, Oeffnen, Kontakte/Fotos/Detailkarte geladen | BESTANDEN |
| 2026-11-09 | Gefunden/behoben: Direktlink ohne bestehende Sitzung lieferte 403 (Sitzungsschluessel erst am Antwortende); Gastsitzungs-Laufzeit wurde in Mitgliedssitzung uebernommen | behoben |
| 2026-11-09 | Parallel-Buchungstest einmal rot: Ursache Testannahme (Objekt 0 kann korrekt erst nach Kontingentende drankommen), Verhalten korrekt; Test korrigiert, Sperrzeilen zusaetzlich vor der Buchungstransaktion angelegt; danach 10x Einzeltest + 2x Gesamtsuite (127) | BESTANDEN |
| 2026-11-09 | Etappe 4: Testsuite 156 Tests (u. a. Betreuer A/B mit manipulierten IDs in URL/POST/Upload/Foto, versteckte Felder, fremder Anbieter, ungueltige Koordinaten, Versionskonflikt, Rechteentzug/Neuzuordnung sofort wirksam, Upload-Regeln, Freigabe erst nach Redaktion, veraltete Freigabe abgewiesen, Rueckmeldung sichtbar/Redaktionsnotiz nicht, Rueckzug sofort/kein Selbst-Republish, Kontaktrueckzug stoppt Mitgliedsauslieferung, Redaktion ohne Konten/Gruppen/OTP-Admin, keine Massenaktionen, Zuweisung nur an Betreuer, Rollenvergabe nur mit frischer Bestaetigung + Protokoll + Sitzungsende, Sperre, keine Hashes/TOTP-Schluessel im Admin, keine Selbstentziehung, Backup-Codes nur mit Reauth, Reauth-Ablauf, E-Mail-Aenderung mit Bestaetigung/Benachrichtigung/Replay-Schutz, paralleles Speichern vs. Freigeben), 2x | BESTANDEN |
| 2026-11-09 | Wettlauf Speichern/Freigeben mit echten DB-Verbindungen, 8x wiederholt | BESTANDEN |
| 2026-11-09 | Gefunden/behoben: Konten-Admin haette veraltete `security_generation` zurueckschreiben koennen (jetzt feldweise Updates); Objekt-Admin ohne Versionsvergleich (jetzt `expected_version` + Sperre) | behoben |
| – | `check --deploy` mit Produktionssettings | OFFEN |
| – | Apache/WSGI-End-to-End | BLOCKIERT (kein Hostingzugang, Profil unbestaetigt) |
| – | Echte SMTP-Zustellung | BLOCKIERT (keine Konfiguration/Freigabe) |

## Entscheidungen

- Rollen: Gruppen `Objektbetreuung` (nur Portal, keine Admin-Rechte) und `Redaktion` (Staff +
  fachliche Modellrechte); Administration = Superuser. Gruppen nur per `ensure_role_groups` bzw.
  bei Rollenvergabe angelegt, nie per Migration/Signal. Policies pruefen bei jeder Anfrage
  verifizierte Sitzung, aktuelle Rolle und aktuelle Zuordnung.
- Portal: Arbeitsfassung mit `expected_version`; jede Aenderung erhoeht `work_version` und setzt
  eingereichte/freigegebene Fassungen auf Entwurf. Rueckzug von Objekt, Kontakten (neue
  disclosure_version) und Fotos sofort; Veroeffentlichung nur durch Redaktion/Administration.
- Admin: OTP-Geraete-, Gruppen- und Axes-Admin entfernt (keine Geheimnisse sichtbar). Konten nur fuer
  Superuser, ohne Passwortfeld; E-Mail nur ueber den Bestaetigungsweg aenderbar. Rollen-/Sperr-
  aenderungen verlangen Reauth, werden protokolliert und beenden bestehende Sitzungen.
- Frische Bestaetigung: Passwort + Code eines eigenen Faktors (App, E-Mail-Code mit Zweck "reauth",
  oder Wiederherstellungscode), 5 min gueltig. Gilt fuer Backup-Codes, E-Mail-Aenderung, Kontenadmin.
- E-Mail-Aenderung: angeforderte Adresse in `pending_email`; OTP-Empfaenger bleibt bis zur
  Bestaetigung die alte Adresse; alte Adresse wird benachrichtigt; Bestaetigung beendet alle Sitzungen.

- Freigabebuchung `DisclosureGrant` (Konto, freigegebener Stand, disclosure_version, 24 h ab erster
  Buchung, keine Verlaengerung). Buchung nur per CSRF-geschuetztem POST; GET zeigt Details nur bei
  gueltiger Buchung der aktuellen Fassung. Kontozeile wird waehrend der Buchung gesperrt, Kontingent-
  ereignis und Buchung in derselben Transaktion. Abgelaufene Buchung derselben Fassung wird neu gebucht.
- Request-Limits: Gaeste ueber kurzlebige Gastsitzung (2 h, Neuanlage 60/h je IP), vollstaendig
  angemeldete Konten ueber das Konto, zusaetzlich IP. JSON/Fotos/Kacheln ohne Sitzung: 403.
  Unbekannte Objekt-IDs zaehlen mit (Enumerationsbremse).
- Fotos: Originale werden nach Verarbeitung verworfen; Varianten `thumb` (<=400 px) und `detail`
  (<=1600 px) als neu kodiertes JPEG ohne Metadaten in `var/private_media/<xx>/<key>/` (0700/0600).
  Gaeste nur `thumb` freigegebener Gastvorschaubilder; Mitglieder mit gueltiger Buchung Gast- und
  Mitgliedsfotos beider Varianten; interne/Entwurfsbilder nur verifizierte Verwaltung bzw. zugeordnete
  Betreuung. Rueckzug (`is_withdrawn`) wirkt sofort.
- Interne Arbeitszugriffe (Etappe 4) verbrauchen kein Besucherkontingent; auf Besucherseiten gelten
  fuer alle Rollen dieselben Buchungsregeln.

- Login/Einrichtung: django-two-factor-auth-Views, abgeleitet und ergaenzt um Kontozustand
  (nur aktive, E-Mail-bestaetigte Konten), sitzungsgebundene E-Mail-Challenges (`LoginChallenge`,
  max. 5 Fehlversuche, neue Challenge ersetzt alte), Versandbudget vor jedem Code, atomaren
  Codeverbrauch (`select_for_update` auf das Geraet) und Konto-/IP-Drosselung fuer Codeeingaben.
  two_factor `profile`/`disable` sind nicht eingebunden (letzter Faktor nicht loeschbar).
- Ohne Faktor fuehrt der Login immer in die Einrichtung (Passwort-only = Einrichtungszustand,
  15 min Leerlauf / 60 min absolut, keine Mitgliedsrechte). Grenze: Wer das Passwort eines
  Kontos ohne Faktor kennt, kann den ersten Faktor einrichten (dokumentiert, Auftrag 11.3).
- Limiter: Ereignistabelle mit rollenden Fenstern, Sperrzeile je Schluessel, HMAC-pseudonymisiert,
  fail closed. Startwerte in `RATE_LIMITS` (settings/base.py); Codeeingaben 10/15 min je Konto,
  50/15 min je IP (eigene Werte).
- E-Mail-Verifikation: signiertes Token (Konto, Adress-HMAC, Sicherheitsgeneration, Zweck,
  Umgebung, 24 h). GET legt nur in die Sitzung und leitet auf tokenfreie URL; Bestaetigung per POST.
- Sitzungen: `security_generation` beendet alle anderen Sitzungen bei Faktor-/Passwortreset/Sperre;
  Leerlauf/Gesamtdauer Mitglieder 60 min/12 h, Arbeitsbereich 30 min/8 h.
- Wiederherstellungscodes: two_factor-Seite (nur vollstaendig angemeldet) zeigt unbenutzte Codes.

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
