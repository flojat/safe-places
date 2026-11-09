# Sicherheit: gepruefte und verbleibende Grenzen

Kein automatischer Produktionssicherheitsnachweis. Getestet ist, was in IMPLEMENTIERUNGSSTATUS.md als BESTANDEN steht.

## Durch automatisierte Tests abgedeckt (Auswahl)

Projektionen ohne interne/Mitgliedsdaten fuer Gaeste; einheitliche 404; Registrierung/Verifikation/Login mit
E-Mail-Code und TOTP inkl. Einrichtung; Codewiederverwendung, Fehlversuche, parallele Challenges, SMTP-Ausfall
ohne Fallback; Admin nur mit zweitem Schritt; Routenpruefung aller Nicht-Admin-Wege fuer Anonyme und
Passwort-only-Sitzungen; atomare Kontingente und Codeverbrauch mit echten parallelen DB-Verbindungen;
Bildpruefung (Bomben, Animation, SVG, EXIF/GPS); Fotorechte; Betreuer A/B-Isolation; Rechteentzug;
Versionskonflikte; swisstopo-Gateway (gemockt) inkl. Budget, Parallelitaet, kein offener Proxy;
Initialkonten; Installationsroutine (Redaktion, TOCTOU, Lock, Archivsicherheit, Rollback-Schutz).

## `check --deploy` (synthetische Produktionswerte)

Verbleibende Hinweise bewusst: W005/W021 (HSTS `includeSubDomains`/`preload` aus, Subdomains unbekannt);
W004 solange `SECURE_HSTS_SECONDS` nicht gesetzt ist (erst nach geprueftem HTTPS setzen).

## Bekannte Grenzen

- E-Mail-Code ist kein unabhaengiger zweiter Faktor; TOTP ist nicht phishingresistent.
- Wer das Passwort eines Kontos ohne Faktor kennt, kann den ersten Faktor einrichten (Auftrag 11.3).
- Wiederherstellungscodes werden von django-otp im Klartext in der DB gespeichert (Paketverhalten); DB-Zugriff
  ist daher besonders zu schuetzen. Die Codes-Seite zeigt unbenutzte Codes in vollstaendig angemeldeter Sitzung.
- Keine Content-Security-Policy (Django 5.2 ohne eingebauten CSP); Templates nutzen keine Inline-Skripte –
  vor Internetbetrieb CSP per Apache-Header ergaenzen.
- Bei identischer Hosting-UID fuer Deployment und Webprozess keine vollstaendige Isolation.
- Logs/Reports: keine Codes, Adressen, Suchtexte; Axes-Logger auf ERROR. Apache-Zugriffslog muss Token-URLs
  ausfiltern (Vorlage).
- Konto-Objekt-Buchungen sind Nutzungsdaten (Bereinigung nach Ablauf + 48 h).
- swisstopo-Proxy/Cache: Bedingungen nicht abschliessend geklaert (vor Veroeffentlichung pruefen).
- Nicht geprueft: echtes Apache/WSGI, HTTPS, SMTP, Hosting-Rechte, Lasttests, Penetrationstest.
