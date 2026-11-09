# Authentifizierung

## Zustaende

`anonymous` -> `pending_email` (registriert, E-Mail unbestaetigt bzw. Initialkonto nicht aktiviert)
-> Login mit Passwort -> `password_verified_mfa_pending` (nur Einrichtung/Logout, keine Mitgliedsrechte,
15 min Leerlauf/60 min absolut) -> zweiter Schritt -> `fully_verified`.

## Registrierung und E-Mail

- Nur E-Mail + Passwort (Django-Validatoren, min. 12 Zeichen). Gleiche Antwort fuer vorhandene Adressen;
  bestehende Konten werden nie veraendert. Normalisierung: trim + Kleinschreibung, keine Alias-Kanonisierung.
- Bestaetigungslink 24 h, signiert (Konto, Adress-HMAC, Sicherheitsgeneration, Zweck, Umgebung). GET legt das
  Token in die Sitzung und leitet auf eine tokenfreie Seite; Bestaetigung per POST (Mail-Scanner-sicher).
- Unbestaetigte Registrierungen werden nach 7 Tagen entfernt (`cleanup_security_state`).

## Zweiter Schritt (fuer alle Rollen)

- Wahl bei der ersten Anmeldung: Authenticator-App (TOTP, empfohlen) oder E-Mail-Code (schwaecher, siehe README).
- E-Mail-Code: nur nach Passwortpruefung, nur an die verifizierte Adresse, 5 min gueltig, 60 s Cooldown,
  an Browsersitzung gebunden (`LoginChallenge`), max. 5 Fehlversuche je Challenge, neuer Code macht den alten
  ungueltig; Budget vor dem Versand; bei Versandfehler kein Passwort-only-Fallback.
- TOTP: QR nur im authentifizierten Setup, Geraet erst nach Bestaetigungscode aktiv, Replay-Schutz,
  paralleler Doppelverbrauch durch Sperre des Geraets verhindert.
- Konto-/IP-Drosselung fuer Codeeingaben (10/15 min je Konto, 50/15 min je IP), django-axes fuer Passwoerter.
- Kein "Browser merken". Sitzungsschluessel wird nach dem Login gewechselt.
- Weitere Methode nur ausdruecklich (`/konto/zweiter-schritt/hinzufuegen/`, frische Bestaetigung). TOTP-only
  erhaelt nie automatisch einen E-Mail-Fallback. Der letzte Faktor ist nicht entfernbar.
- Wiederherstellungscodes: 10 einmalige Codes, Erzeugung nur mit frischer Bestaetigung; Nutzung wird protokolliert
  und per Mail gemeldet.

## Sitzungen

Mitglieder 60 min Leerlauf / 12 h absolut, Arbeitsbereich 30 min / 8 h. `security_generation` beendet alle anderen
Sitzungen bei Passwortreset, Faktoraenderung, E-Mail-Wechsel, Rollen-/Sperraenderung. Logout nur per POST,
leert den Browsercache (`Clear-Site-Data`).

## Sensible Aenderungen (frische Bestaetigung, 5 min)

Passwort + Code eines eigenen Faktors (App, E-Mail-Code mit Zweck "reauth" oder Wiederherstellungscode) fuer:
Wiederherstellungscodes, weitere/entfernte Methode, E-Mail-Wechsel, Kontenverwaltung durch die Administration.

## Wiederherstellung

- Passwortreset (Django-Ablauf, 1 h, Budget): meldet nicht an, beendet Sitzungen, aendert keine Faktoren.
  TOTP-only bleibt TOTP-only. Nicht fuer unbestaetigte oder nicht aktivierte Konten.
- E-Mail-Wechsel: neue Adresse per Link bestaetigen; bis dahin bleibt die alte Adresse OTP-Empfaenger und wird
  benachrichtigt; danach alle Sitzungen beendet.
- **Administrativer Notfallprozess** (Verwaltung -> Benutzer -> "Kontowiederherstellung"): nur nach
  Identitaetspruefung ausserhalb der Anwendung (z. B. persoenlich/telefonisch ueber bekannte Kontaktdaten),
  Bestaetigung der Pruefung im Formular und frische Bestaetigung der Administration. Wirkung: alle Faktoren und
  Wiederherstellungscodes entfernt, alle Sitzungen beendet, E-Mail muss neu bestaetigt werden; danach regulaerer
  Login und neue Faktorwahl. Die Administration erfaehrt kein Passwort/Geheimnis; Vorgang protokolliert und gemeldet.

## Mailausfall

Konten bleiben pending, Logins mit E-Mail-Code sind nicht moeglich (kein Fallback). TOTP-Konten sind nicht betroffen.
Produktiv kein Offline-"verifiziert"-Schalter und keine MFA-Abschaltung.
