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
| 0 | Preflight/Hostingpruefung (`scripts/manage_installation.py`) | umgesetzt (Etappe 6); Zielhosting nicht geprueft |
| 0/1 | Projektgeruest, Lockdateien mit Hashes, Settings local/test/production, Benutzermodell, erste Migrationen | umgesetzt (siehe Pruefungen) |
| 1 | Datenmodell Objekt/Anbieter/Snapshot/Foto, drei Projektionen, Gastkarte/-liste, Leaflet lokal, seed_demo | umgesetzt; Gast-Requestlimits und Bildverarbeitung OFFEN (Etappe 3) |
| 2 | Registrierung, E-Mail-Bestaetigung, E-Mail-/TOTP-Login, Sitzungsschutz, Passwortreset, atomarer Limiter | umgesetzt; offen: Faktorwechsel/E-Mail-Aenderung mit frischer Reauth, Benachrichtigungsmails, Recovery-Code-Nutzungsprotokoll (Etappe 4/6) |
| 3 | Freigabebuchungen/Kontingente, Mitgliedsdetails mit Kontakten, sichere Bildverarbeitung, Fotoauslieferung, Gast-/Mitglieds-Requestlimits | umgesetzt; Upload-UI folgt mit Etappe 4 (Portal) |
| 4 | Betreuerportal, Redaktion (Fassungsvergleich/Freigabe/Zurueckweisung), Administration (Rollen/Sperren), frische Bestaetigung, E-Mail-Aenderung | umgesetzt; offen: Benachrichtigung bei Faktoraenderung/Recovery-Code-Nutzung per Mail, zusaetzlichen Faktor nachtraeglich einrichten, administrativer Recovery-Prozess (Etappe 6/7) |
| 5 | swisstopo-Gateway mit Cache/Generationen, globale Provider-Budgets und Slots, cacheUpdate-Pruefung, Adresssuche im Arbeitsbereich | umgesetzt (gemockt getestet); echter Smoke-Test BLOCKIERT bis Freigabe; lokal weiterhin `MAP_TILE_SOURCE=local_test` |
| 6 | Initialkonten (Provisionierung, Einladung, Aktivierung, Rollen nach zweitem Schritt), Installations-/Pruef-/Deploy-/Rollback-Routine, Hosting-Vorlagen | umgesetzt; echte Apache/WSGI- und SMTP-Pruefung BLOCKIERT (kein Hostingzugang) |
| 7 | Zusatzfaktor/Faktorentfernung, Sicherheitsbenachrichtigungen, Admin-Wiederherstellung, Routenpruefung, Restore-Test, vollstaendige Dokumentation | umgesetzt |

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
| 2026-11-09 | Doku/Capabilities geprueft: Layer `ch.swisstopo.pixelkarte-farbe`, TileMatrixSet 3857_19 (Zoom 0-19), JPEG, Time `current`; cacheUpdate-Endpunkt; SearchServer `sr=4326`; Nutzungsbedingungen (Fair Use, Quellenangabe) – 1 Metadatenabruf GetCapabilities | erledigt |
| 2026-11-09 | Etappe 5: Testsuite 181 Tests (Provider gemockt: feste URL, Query-Parameter wirkungslos, Cachetreffer ohne Budget, ungueltige Antworten nicht gecacht aber gezaehlt, Budgetende = 0 Abrufe, Limiterausfall = kein Abruf, veraltete Kachel nicht ausgeliefert, nur Grundkarten im Cache, keine Providerreferenz im Browser-JS, Weiterleitungen verweigert, Slots/Leases, Generation bei cacheUpdate-Aenderung, Cache-Bereinigung; parallel: gleicher Miss genau 1 Abruf, max. gleichzeitige Abrufe, globales Budget unter Last; Adresssuche nur Betreuer eigenes Objekt/Redaktion, feste Parameter, HTML entfernt, Koordinaten validiert, kein Logging, Konto-/REST-Budget), 2x; Concurrency 6x | BESTANDEN |
| 2026-11-09 | VORFALL: ein ungemockter Altest aus Etappe 1 (`MAP_TILE_SOURCE=swisstopo`) loeste in 2 Gesamtlaeufen vermutlich je 1 echten Kachelabruf an wmts.geo.admin.ch aus (nicht freigegeben, max. 2 Abrufe). Behoben: harte Netzwerksperre `SWISSTOPO_NETWORK_ENABLED=False` in Testsettings, Test prueft fehlenden Netzzugriff | behoben |
| 2026-11-09 | Echter swisstopo-Smoke-Test nach Freigabe (separat, genau 3 Abrufe ueber Anwendungscode): Kachel z12 Bern = gueltiges JPEG 33.6 KB/0.11 s; cacheUpdate = 2026-11-06T13:57:25 (erste Pruefung -> Generation 2); SearchServer "Bundesplatz 3 Bern" = 5 Treffer, erster in CH-Rechteck; Budgetzaehlung 1 Kachel/2 REST | BESTANDEN |
| 2026-11-09 | Etappe 6: 21 Bootstrap-Tests (Plan ohne Aenderung/Adressen, pending ohne Passwort/Rechte, Wiederholung ohne Reset/Entsperren, Konflikt bei Fremdregistrierung inkl. Gross-/Kleinschreibung, keine Wiederherstellung geloeschter Konten, Manifestfehler ohne Adressen, Umgebungsabgleich, GET verbraucht nichts, einmalige Aktivierung, neue Einladung invalidiert alte, Token nicht als Verifikation/fremde Umgebung nutzbar, Reset umgeht Aktivierung nicht, SMTP-Fehler bleibt pending, Produktionsversand nur mit Bestaetigung, Rollen erst nach TOTP/E-Mail-Einrichtung, Registrierung erst nach aktivem Admin, parallele Provisionierung ohne Duplikate) | BESTANDEN |
| 2026-11-09 | Etappe 6: 17 Tests Installationsroutine (Redaktion in Berichten, lesender Preflight, BLOCKED ohne WSGI, Umgebungsabgleich, Vendor-Manipulation, Backup-Pflicht, Plan/Apply, PREPARED_NOT_ACTIVATED, Ref-Aenderung zwischen Plan/Apply, Ref-Injektion, Lock, Symlink-Ausbruch im Archiv, Reload-Fehler mit Rueckverlinkung, Rollback bei neuerem Schema verweigert, Bedienfehler) | BESTANDEN |
| 2026-11-09 | Lokal echt ausgefuehrt: `preflight`, `install --plan`, `doctor` (local); echter `deploy --plan/--apply` in temporaeres app-home (Git-Export, neue venv, Hash-Installation, collectstatic, Symlink, simulierter Reload) | BESTANDEN (WSGI-Ausfuehrung UNKNOWN) |
| 2026-11-09 | `manage.py check --deploy` mit Produktionssettings und synthetischen Werten: nur W005/W021 (HSTS-Subdomains/Preload bewusst aus); ohne HSTS-Sekunden zusaetzlich W004; Test-Mailbackend wird abgewiesen | BESTANDEN mit bewerteten Hinweisen |
| 2026-11-09 | Gesamtsuite 219 Tests, 2x | BESTANDEN |
| 2026-11-09 | Gefunden/behoben: Testsettings uebernahmen `MAP_TILE_SOURCE` aus der lokalen .env (Netzsperre griff, keine Abrufe); Testsettings fixieren jetzt Kartenquelle und Registrierungssperre | behoben |
| 2026-11-09 | Lokale Initialkonten aus privatem Manifest provisioniert (4x create) und Einladungen in die lokale Datei-Mailablage versendet (keine echte Zustellung); Status invited | erledigt |
| 2026-11-09 | Gefunden/behoben: lokale Mailablage war 0755 (Einladungslinks fuer andere Benutzer lesbar) -> 0700, local.py erzwingt 0700, `doctor` warnt bei Gruppen-/Fremdzugriff | behoben |
| 2026-11-09 | Etappe 7: 10 Tests (Zusatzfaktor nur mit Reauth und nur fehlende Methode, Login-Alternative nur nach ausdruecklicher Einrichtung, letzter Faktor nicht entfernbar, Entfernen mit Reauth/Abmeldung/Mail, Backup-Code einmalig + protokolliert + gemeldet, Admin-Wiederherstellung mit Reauth/Identitaetsbestaetigung ohne Passwortkenntnis, Redaktion ohne Wiederherstellung, Routenpruefung aller Nicht-Admin-Wege GET/POST fuer Anonyme und Passwort-only-Superuser, Einrichtungszustand ohne Mitgliedsdaten) | BESTANDEN |
| 2026-11-09 | Gefunden/behoben: `SetupView` sperrte vorhandene Faktoren nur bei GET – per POST waere ohne Reauth ein weiterer Faktor einrichtbar gewesen; Methodenauswahl jetzt auf verfuegbare Methoden beschraenkt | behoben |
| 2026-11-09 | Gesamtsuite 229 Tests, 2x | BESTANDEN |
| 2026-11-09 | Restore-Test: Dump Dev-DB -> isolierte Wegwerf-DB, 32 Tabellen ohne Pruefsummenabweichung, Zeilen identisch; 204 Mediendateien hashgleich; danach geloescht | BESTANDEN |
| 2026-11-09 | Kompatibilitaet Zielpython: Gesamtsuite in Debian-12-Container mit Python 3.11.18 gegen lokale Test-DB, Hash-Installation inkl. Quellbau mysqlclient 2.3.0: 229 Tests, 5 uebersprungen (Browser ohne Playwright im Container). Erster Lauf ohne git im Container: 11 Installationstests rot (Umgebung), mit git gruen. Hinweis: Server hat Debian-Python 3.11.2 | BESTANDEN |
| 2026-11-09 | Zielserver vorbereitet (durch Betreiber): python3.11-venv, python3.11-dev, libmariadb-dev, pkg-config; mod_wsgi entfernt; Profil Passenger gewaehlt | erledigt |
| 2026-11-10 | Zielserver: `preflight --environment production` (inkl. `--network-check`) | BESTANDEN (WSGI-Laufzeit/SMTP UNKNOWN) |
| 2026-11-10 | Zielserver: `deploy --apply` v0.5.0-rc1 brach ab: `tarfile.extractall(filter=...)` fehlt in Debian-Python 3.11.2 (erst ab 3.11.4). Kein Release angelegt, nichts aktiviert. Lehre: Kompatibilitaetstest lief mit 3.11.18 statt der exakten Zielversion | FEHLGESCHLAGEN, behoben |
| 2026-11-10 | Behebung: eigene strikte Pruefung der Archiveintraege fuer Python ohne Extraktionsfilter; Gesamtsuite im Container mit exakt `python3.11 3.11.2-6+deb12u9` (Fallback aktiv): 233 Tests, 5 Browsertests uebersprungen | BESTANDEN |
| 2026-11-10 | Zielserver: `deploy --apply --migrate` v0.5.0-rc2 (Export, venv, Hash-Installation inkl. mysqlclient-Build, Checks, collectstatic, Migration, Aktivierung per restart.txt); `doctor` production: WARN (1 Deploy-Hinweis, Initialkonten fehlen) | BESTANDEN |
| 2026-11-10 | Zielserver: Passenger-Direktiven in ISPConfig; echte Requests (durch Betreiber): Start 200, /static CSS 200 text/css, Login 200, HTTP->HTTPS 301; /.env, shared, private, var, media, releases, repository/.git, /static/../ jeweils 404 | BESTANDEN |
| 2026-11-10 | Befunde: HEAD auf Startseite 405 (require_GET); Release-/staticfiles-Rechte folgten umask 077 (manuell auf 710/750/640 gesetzt); manage.py auf dem Server braucht ADRESSKARTE_ENV_FILE | behoben in v0.5.0-rc3 (236 Tests lokal und mit Debian-Python 3.11.2) |
| 2026-11-10 | rc3 (HEAD, .env-Erkennung im Release, Rechte unabhaengig von umask): 236 Tests lokal und mit Debian-Python 3.11.2; auf Zielserver deployt: HEAD 200, CSS 200, Release 710, doctor ohne Umgebungsvariable, check --deploy nur W004 (HSTS) | BESTANDEN |
| 2026-11-10 | Zielserver: SMTP-Testversand scheitert mit `DH_KEY_TOO_SMALL` (externer MX im selben Netz, schwache DH-Parameter); Betreiberentscheid: Client anpassen | FEHLGESCHLAGEN |
| 2026-11-10 | rc4: SMTP-Backend mit Profilen (`no_dhe` ohne Absenkung, `legacy_dh` als dokumentierte Abschwaechung; Zertifikats-/Hostnamenpruefung immer aktiv), private Ordner unabhaengig von umask, genauere Manifestmeldungen; 243 Tests lokal und mit Debian-Python 3.11.2/OpenSSL 3.0.22 | BESTANDEN |
| 2026-11-10 | Zielserver: Initialkonten-Plan (4x create) | BESTANDEN |
| 2026-11-10 | Zielserver rc4: `no_dhe` scheitert (MX ohne ECDHE); openssl bestaetigt TLS 1.2 nur mit DHE. Betreiberentscheid `EMAIL_TLS_PROFILE=legacy_dh`; echter Testversand an Betreiber-Testadresse zugestellt (Zertifikats-/Hostnamenpruefung aktiv) | BESTANDEN mit dokumentierter Abschwaechung |
| 2026-11-10 | Zielserver: Initialkonten provisioniert, admin=complete, Einladungen editor/object_manager/visitor versendet; HSTS gesetzt; Cronjobs per crontab; `check_tile_cache_update` scheitert (Timeout/502) | teilweise |
| 2026-11-10 | Diagnose auf Zielserver: api3 haengt/502 nur bei Kombination eigener User-Agent + `Accept: application/json`; mit `Accept: */*` 200 in 0,04 s (`application/geo+json`) | Ursache extern |
| 2026-11-10 | rc5: REST-Abrufe (cacheUpdate, Adresssuche) mit `Accept: */*`, Antwortpruefung unveraendert; W005/W021 in Produktion bewusst stummgeschaltet; 243 Tests lokal | BESTANDEN |
| 2026-11-10 | Zielserver rc5: deploy --apply, Start 200, `check_tile_cache_update` OK (neue Generation), check --deploy ohne Hinweise (2 silenced), Adresssuche im Browser OK; doctor WARN nur SMTP legacy_dh; Initialkonten alle complete | BESTANDEN |
| 2026-11-10 | Redaktionelle Direktfreigabe (Betreiberentscheid): Pflicht-Betreuung; nie freigegebene Objekte nur nach Einreichung, ausser Standard-Betreuer (`DEFAULT_OBJECT_MANAGER_EMAIL`); Direktfreigabe gesperrt bei offenen Betreuungsaenderungen und zurueckgezogenen Objekten; Mitteilungen an Betreuung (Portal + Verwaltung, Entscheide per Mail, eigenes Budget); Redaktion darf Objekte anlegen und Fotos hochladen/entfernen; Rueckzug im Inline schreibgeschuetzt; Migration 0009; 260 Tests lokal | BESTANDEN (Zielserver ausstehend) |
| 2026-11-10 | Mehrere Kategorien je Objekt (`categories` JSON, Migration 0010 uebernimmt Einzelkategorie), Filter als Checkboxen (ODER, mehrere Werte), Trefferliste als Karten (Bild, Titel, Ort, Kategorien mit Symbol, Text, Pfeil), Listenspalte ca. 45 % breit; Portal-/Admin-Formulare mit Checkboxen; 275 Tests inkl. Browsertest und Migrationstest; Screenshots Desktop/Mobil lokal geprueft | BESTANDEN (Zielserver ausstehend) |
| 2026-11-10 | Kategorien pflegbar (Model `Category`, nur Administration, Symbol/Reihenfolge/aktiv, Kennung fest, kein Loeschen; Migration 0011 legt die 6 bisherigen an). Einzelfreigaben fuer nicht angemeldete Besucher (Langbeschreibung, Details, Kontakte; Standard aus; wirksam ab Freigabe; Besucherkontakte nur mit Mitgliederfreigabe; Kontaktrueckzug sofort). **Abweichung vom Auftrag (Betreiberentscheid 2026-11-10):** Gastprojektion kann diese Felder je Objekt enthalten; Liste/API weiterhin ohne. 286 Tests | BESTANDEN (Zielserver ausstehend) |
| 2026-11-10 | Bildergalerie (Lightbox, `static/js/gallery.js`) auf Vorschau, Mitgliederdetail und Listenkarten; Inhalt je Berechtigung (Gaeste/Mitglieder ohne Buchung: Gastbilder; mit Buchung: Gast- und Mitgliederbilder; nie intern). **Betreiberentscheid:** Gastbilder auch in Galeriegroesse (1600 px) fuer alle. Alt-Text heisst "Bildbeschreibung" (Bildunterschrift + Alt). Pflichtbestaetigung Bildrechte beim Upload (Portal + Verwaltung, `rights_confirmed_at`, Protokoll) und Copyright-Hinweis; Migration 0012; 296 Tests inkl. Browsertest Lightbox | BESTANDEN (Zielserver ausstehend) |
| 2026-11-10 | Punktwahl auf Karte (Popup) neben der Adresssuche in Portal und Verwaltung, Koordinaten werden ins Formular uebernommen, Server prueft weiterhin; Grundkarte Luftbild (SWISSIMAGE `ch.swisstopo.swissimage`) umschaltbar auf Gastkarte, Detailkarte und Punktwahl, eigener Kachelweg `/map/luftbild/`, eigener Cache/Generation, cacheUpdate und Bereinigung je Layer; 306 Tests inkl. Browsertests (Testkacheln). Realer Abruf SWISSIMAGE noch nicht geprueft (Smoke-Test auf Zielserver nach Freigabe) | BESTANDEN lokal |
| 2026-11-10 | Beobachtet: in Browsertests vereinzelt "Internal Server Error" auf Kachelwegen bei parallelen Abrufen (Live-Testserver), schon vor dieser Aenderung; Tests bestehen | OFFEN (untersuchen) |
| 2026-11-10 | Zielserver rc6: Backup, `deploy --apply --migrate --backup-confirmed` (0009-0012), `ensure_role_groups`, check --deploy ohne Hinweise (2 silenced), `check_tile_cache_update` beide Layer OK (SWISSIMAGE cacheUpdate real erreichbar), doctor WARN nur SMTP, Start 200 | BESTANDEN |
| – | MX auf >= 2048-Bit-DH oder ECDHE aktualisieren lassen, danach `EMAIL_TLS_PROFILE=default` | OFFEN (externer MX-Betreiber) |

## Entscheidungen

- swisstopo: nur Layer `ch.swisstopo.pixelkarte-farbe` (Zoom 7-18 genutzt), feste Ziele
  `wmts.geo.admin.ch` und `api3.geo.admin.ch`, TLS, keine Redirects/Proxies, 5 s Timeout,
  max. 512 KiB/Kachel. Budgets global in der DB: Kacheln 300/min, 20'000/24 h; REST 20/min,
  200/24 h; Adresssuche zusaetzlich 6/min je Konto; max. 6 gleichzeitige Abrufe (Slots mit Lease).
- Cache: `var/tile_cache/<layer>/<generation>/z/x/y.jpg`, max. 7 Tage alt, Groesse begrenzt
  (`cleanup_tile_cache`), Generation wechselt bei geaendertem cacheUpdate (`check_tile_cache_update`,
  per Cron). Paralleler Miss: DB-Lease je Kachel, andere warten max. 3 s auf die Datei.
- Bei Budgetende/Ausfall: 503 ohne Direkt-Fallback; Liste bleibt nutzbar. Keine veralteten Kacheln.
- Offen vor Veroeffentlichung: Proxy-/Cache-Integration und Gesamtverkehr gegen aktuelle
  swisstopo-Bedingungen pruefen, bei Unklarheit swisstopo kontaktieren (Auftrag 5).

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


## Stand Inbetriebnahme Zielserver (2026-11-10, Feierabend)

Laeuft produktiv ueber Passenger, aktives Release **v0.5.0-rc6** (Initialkonten alle aktiv, Cronjobs per crontab, HSTS gesetzt); HTTPS, statische Dateien und Sperren der
privaten Pfade geprueft; SMTP-Testversand zugestellt (`EMAIL_TLS_PROFILE=legacy_dh`, siehe oben).

Naechste Schritte (Befehle in der Sitzung vorbereitet, Ausfuehrung durch den Betreiber):
1. Falls noch nicht erfolgt: `provision_initial_accounts ... --apply` (Plan zeigte 4x create).
2. `hosting.local.json`: `"smtp_verified": true`.
3. `send_initial_invitations --plan`, dann zuerst nur `--role admin --apply --confirm-send`; Admin aktiviert,
   richtet Authenticator-App ein, prueft "Verwaltung"; danach die uebrigen drei einladen.
4. Cronjobs in ISPConfig (cleanup_security_state stuendlich, check_tile_cache_update 6-stuendlich,
   cleanup_tile_cache taeglich); `SECURE_HSTS_SECONDS=3600` setzen, spaeter erhoehen; abschliessend `doctor`.
5. Offen extern: MX-Aktualisierung (DH >= 2048 / ECDHE), dann `EMAIL_TLS_PROFILE=default`.

## Abnahme lokaler Prototyp (Auftrag 14)

| Kriterium | Stand |
|---|---|
| Startet dokumentiert lokal mit MariaDB, 100 Demos, 90 Vorschauen in synchroner Karte/Liste | erfuellt (Tests + Browsertests) |
| Gaeste suchen begrenzt, ohne Mitgliederkontakte/Langdetails | erfuellt |
| Selbst registrierter Besucher sieht nach Verifikation und Passwort + E-Mail-Code ODER TOTP Details/Kontakte | erfuellt (E2E beider Wege, Browser) |
| Alle Rollen mit demselben verpflichtenden zweiten Schritt, kein Admin-/Setup-/Reset-/API-Bypass | erfuellt (Routenpruefung, Admin-, Reset-, Setup-Tests) |
| Kontingente kontoweit und atomar; unbefugte Fotos/Details nicht ausgeliefert; Wiederabruf funktioniert | erfuellt (inkl. Parallelitaet) |
| Betreuer erfassen/einreichen, Redaktion prueft/freigibt, Administration verwaltet ohne Budgetumgehung | erfuellt |
| Entwuerfe intern; Rueckzug/Rechteentzug stoppen neue Zugriffe sofort | erfuellt |
| swisstopo- und Mailbudgets auch bei mehreren Workern; kein offener Proxy, kein Direkt-Fallback, kein Mailrelay | erfuellt (gemockt, parallel); 1 freigegebener Smoke-Test |
| Kernpruefungen wirklich ausgefuehrt, Blockaden benannt, lokaler Mailtest vs. echte Zustellung dokumentiert | erfuellt |
| Vier Initialkonten bereitstellbar, selbst Passwort und Methode, Wiederholung/Kollision/Deployment ohne Rechte-/Sicherheitsresets | erfuellt (lokal provisioniert, Aktivierung durch Betreiber ausstehend) |
| Projektlokale Installation und lesende Pruefung; Git-Deployment/Releasewechsel mit Fehler-/Rueckfalllogik in isolierter Umgebung geprueft | erfuellt (temporaeres app-home) |

## Vor einer Produktionsfreigabe zwingend offen

1. Hosting: bestaetigte WSGI-Anbindung (mod_wsgi/Passenger) mit passendem Python, App-Reload, private Verzeichnisse,
   Shell/Git, MariaDB-Konto; echter Webrequest gegen das Release (docs/HOSTING_CHECK.md).
2. HTTPS, danach HSTS; Content-Security-Policy per Apache-Header.
3. Echter SMTP-Dienst mit TLS, Zustellbarkeitstest an freigegebenen Testempfaenger.
4. Restore auf dem Zielhosting testen; Backup-Plan mit Betreiber.
5. swisstopo: Proxy-/Cache-Integration und erwarteter Gesamtverkehr gegen die Bedingungen pruefen bzw. mit swisstopo klaeren.
6. Limits bei normaler Bedienung messen und festlegen; Netzwerk-/DDoS-Schutz separat.
7. Aktivierung der Initialkonten durch die Personen selbst auf der Zielinstanz; Betreiberentscheid zum Go-live.
