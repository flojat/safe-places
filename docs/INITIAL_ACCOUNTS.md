# Initialkonten

Vier Identitaeten werden ausdruecklich vorbereitet: Besucher, Objektbetreuung, Redaktion,
Administration. Es gibt kein Standardpasswort und keinen zusaetzlichen Superuser
(`createsuperuser` ist deaktiviert).

## Manifest

- Versioniert: `initial_accounts.example.json` (nur Beispieldomains).
- Privat: `initial_accounts.local.json` (lokal) bzw. `<app-home>/shared/config/` (Hosting),
  Rechte 0600, **nie in Git**, nie in Fixtures, Screenshots oder Berichten.
- Format: `{"accounts": [{"email": "...", "role": "visitor|object_manager|editor|admin"}]}` –
  genau vier Eintraege, jede Rolle genau einmal.

## Ablauf

    manage.py provision_initial_accounts --manifest <datei> --environment <env> --plan
    manage.py provision_initial_accounts --manifest <datei> --environment <env> --apply
    manage.py send_initial_invitations --environment <env> --base-url https://<host> --plan
    manage.py send_initial_invitations --environment <env> --base-url https://<host> --apply [--role admin]
    # Produktion zusaetzlich: --confirm-send

1. **Provisionierung**: Konto mit unbrauchbarem Passwort, unbestaetigter E-Mail, Zustand
   `pending_activation`, ohne Rechte. Zielrolle geschuetzt in `InitialAccount`. Ausgabe nur
   Rolle + Aktion (`create`, `unchanged`, `conflict`, `removed`).
2. **Wiederholung/Parallelitaet**: keine Duplikate, keine neuen Passwoerter, kein Entsperren,
   keine Rollenaenderung. Geloeschte Konten werden nicht wiederhergestellt (`removed`).
3. **Konflikt**: Existiert die (normalisierte) Adresse bereits aus einer anderen Registrierung,
   wird nichts vergeben. Aufloesung nur nach Identitaetspruefung durch den Betreiber.
4. **Einladung**: Link 24 h gueltig, einmalig, gebunden an Konto, Adresse, Zweck, Umgebung,
   Einladungs- und Sicherheitsgeneration. Neue Einladung invalidiert die alte; Mailbudgets gelten.
   GET legt das Token nur in die Sitzung (Mail-Scanner verbrauchen nichts); Aktivierung per POST.
5. **Aktivierung**: Passwort setzen, Adresse gilt als bestaetigt. Keine Sitzung, keine Rolle.
6. **Login + zweiter Schritt**: Die Person waehlt E-Mail-Code oder Authenticator-App. Erst danach
   wird die hinterlegte Rolle vergeben (Protokolleintrag `role_grant bootstrap:<rolle>`).

Der Standard-Passwortreset funktioniert fuer nicht aktivierte Konten nicht. Lokal gehen Mails in
die private Ablage `var/private_mail/` (keine echte Zustellung). Produktiv bleibt die
Registrierung geschlossen, bis ein Administrator vollstaendig aktiv ist
(`REGISTRATION_REQUIRES_ADMIN`). Initialsetup auf jeder Zielinstanz separat; lokale Konten,
Passwoerter oder TOTP-Geraete werden nie per Git/DB-Kopie uebernommen.
