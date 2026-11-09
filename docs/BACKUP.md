# Sicherung und Wiederherstellung

Git ist nur Codehistorie – kein Backup von Datenbank, Fotos oder Geheimnissen.

## Zu sichern (konsistent, getrennt vom Webroot)

1. **Datenbank**: `mariadb-dump --single-transaction --no-tablespaces --default-character-set=utf8mb4 <db>`
   mit Zugangsdaten aus einer 0600-Optionsdatei (nicht auf der Kommandozeile).
2. **Private Medien**: `VAR_DIR/private_media/` (Tar-Archiv, Pruefsummenliste).
3. **Geheimnisse**: `shared/config/.env`, `hosting.local.json`, Initialmanifest – verschluesselt, getrennt aufbewahren.

Nicht sichern bzw. ersetzbar: Tile-Cache, private Testmailablage (nur Entwicklung), venvs, staticfiles.

## Wiederherstellung

- Immer zuerst in eine **isolierte** Datenbank/Umgebung, nie direkt in Produktion ueber laufende Daten.
- Nach einem Restore alter Staende: Sicherheitszustaende pruefen/invalidieren (Sessions loeschen, Login-Challenges,
  ausstehende Einladungen, Buchungen, Limitereignisse); Providerbudgets nicht versehentlich zuruecksetzen.
  Empfehlung: `DELETE FROM django_session`, `cleanup_security_state`, neue Einladungen nur ausdruecklich.
- Kein stilles Restore durch das Deployment (siehe DEPLOYMENT.md).

## Getestet (lokal, 2026-11-09)

Dump der Entwicklungsdatenbank -> Restore in isolierte Wegwerf-DB (`<testdb>_restorecheck`) -> 32 Tabellen,
0 Pruefsummenabweichungen, Zeilenzahlen identisch; 204 Mediendateien gesichert und hashgleich wiederhergestellt;
Wegwerf-DB, Dump und Zugangsdateien danach geloescht. Ein Restore auf dem Zielhosting ist noch nicht getestet.
