# Deployment

## Verzeichnisplan (Vorschlag, mit Hoster abzustimmen)

    <app-home>/
      repository/                Git-Quelle (nur lesend, Deploy-Key)
      releases/<commit12>-<zeit>/  konkreter Stand mit eigener .venv und staticfiles/
      current -> releases/<id>/    nur wenn das Hosting Symlinks korrekt unterstuetzt
      shared/config/             .env, hosting.local.json, Initialmanifest (0700/0600)
      shared/locks/              Deploymentlock
      shared/installation-state/ Plan und Releasehistorie
    var/ (bzw. VAR_DIR)          private_media, tile_cache, private_mail (nur Entwicklung)

Apache erhaelt nur `current/config/wsgi.py` und `current/staticfiles/`
(`deployment/apache/adresskarte-mod_wsgi.conf.example`, Passenger: `deployment/passenger_wsgi.py.example`).

## Ablauf

1. Lokal: Tests gruen, Commit, freigegebener Tag.
2. Betreiber holt den Tag in `repository/` (kein automatisches Deploy bei Push).
3. `manage_installation.py deploy --environment production --app-home <app-home> --ref <tag> --plan`
4. `... --apply` – exportiert per `git archive` (keine Hooks/Submodule, sicheres Entpacken),
   neue venv, `--require-hashes`, Assets, `check --deploy`, `collectstatic`, Migrationsplan.
5. Offene Migrationen: Release bleibt **vorbereitet**. Nach verifiziertem Backup und
   abgestimmtem Wartungsfenster: `--apply --migrate --backup-confirmed`. MySQL-DDL ist nicht
   transaktional; bei Teilfehler Stopp und Diagnose, kein Auto-Restore.
6. Aktivierung nur mit vom Betreiber bestaetigter Reload-Funktion; sonst `PREPARED_NOT_ACTIVATED`
   mit manuellem Schritt. Scheitert der Reload, wird das vorherige Release wieder verlinkt.
7. Autorisierten Webrequest pruefen (docs/HOSTING_CHECK.md).

## Rollback

`rollback --to <release-id> --plan/--apply` nur, wenn das aktuelle Release keine Migrationen
enthaelt, die das Ziel nicht kennt. Sonst Wartungsmodus und Betreiberentscheid. Keine
stille Datenbankwiederherstellung (Sicherheitszustaende, Quoten, Tokens).

## Produktive Konfiguration

`DEBUG=False`, explizite `ALLOWED_HOSTS`, HTTPS, sichere Cookies, SMTP mit TLS (Test-/Datei-
Backends werden abgewiesen), `REGISTRATION_REQUIRES_ADMIN` aktiv. HSTS (`SECURE_HSTS_SECONDS`)
erst nach geprueftem HTTPS setzen; `includeSubDomains`/`preload` bewusst aus (Subdomains unbekannt).
`TRUST_X_FORWARDED_PROTO` nur bei bestaetigter Proxykette. swisstopo-Cache-/Proxy-Integration vor
Veroeffentlichung gegen die aktuellen Bedingungen pruefen (bei Unklarheit swisstopo kontaktieren).
