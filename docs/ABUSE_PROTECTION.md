# Missbrauchsschutz

Alle Zaehler persistent und atomar in MariaDB (`locations/rate_limits.py`): rollende Fenster, Sperrzeile je
Schluessel, HMAC-pseudonymisierte Schluessel, fail closed (Limiterausfall -> keine neue Freigabe, keine Mail,
kein Providerabruf). Werte in `RATE_LIMITS` (config/settings/base.py) sind eigene Startwerte, keine Anbietervorgaben.

| Zugriff | Gast (Sitzung) | Mitglied (Konto) | IP / global |
|---|---|---|---|
| Vorschau/Liste/Kartenpunkte | 12/min, 120/24 h | 60/min, 1000/24 h | 300/min, 5000/24 h je IP |
| Neue Objektfreigaben | – | 5/15 min, 20/24 h | 40/15 min, 100/24 h je IP; 500/24 h global |
| Details (gebucht) | – | 60/min | 300/min je IP |
| Fotos | 60/min | 240/min | 1200/min je IP |
| Kacheln (inkl. Cache) | 120/min | 360/min | 1200/min je IP |
| Registrierung | 3/h, 5/24 h je IP | – | 100/24 h global |
| Sicherheitsmails | – | 5/h, 15/24 h je Konto und Empfaenger; 60 s je Zweck | 30/h, 100/24 h je IP; 30/min, 500/24 h global |
| Portal | – | 30 Schreibaktionen/min, 20 Entwuerfe/24 h, 10 Uploads/h, 50/24 h | 120/min, Uploads 30/h je IP |
| swisstopo | – | Adresssuche 6/min je Konto | Kacheln 300/min, 20'000/24 h; REST 20/min, 200/24 h; max. 6 parallel |

- Gaeste: kurzlebige technische Sitzung (2 h); Neuanlage 60/h je IP; ohne Sitzung keine Daten und kein Upstream.
- IP nur aus der Verbindung (IPv6 als /64); Forwarded-Header werden ignoriert, bis eine Proxykette bestaetigt ist.
- Konten sind kein Menschennachweis; alle Sitzungen eines Kontos teilen das Kontingent.
- Statische Dateien, Login, Registrierung und Hilfe zaehlen nicht zum Suchbudget.
- Bereinigung: `cleanup_security_state` (Ereignisse > 48 h, Challenges, Buchungen, Sitzungen, unbestaetigte Konten).
- **Notabschaltung**: `MAP_TILE_SOURCE=local_test` in der privaten .env stoppt sofort alle Kachelabrufe (nach
  App-Reload). Limits stehen in `RATE_LIMITS` (Settings, Aenderung per Release); einzelne Konten sperrt die
  Administration. Registrierung: `REGISTRATION_REQUIRES_ADMIN` bzw. Limit `register_global`.
- Kein Versprechen gegen Scraping oeffentlicher Vorschauen, Mehrfachregistrierungen, Phishing oder DDoS;
  Netzwerkschutz ist separat vor Internetbetrieb zu pruefen.
