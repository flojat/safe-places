# Quellen und Pruefstand (2026-11-09)

- Django 5.2 (LTS) Doku: Deployment-Checkliste, mod_wsgi, Datenbanken/MySQL, Migrationen, Auth, Tests.
- django-two-factor-auth 1.18.1 und django-otp 1.7.5 (installierter Quellcode geprueft: LoginView/SetupView,
  EmailDevice-Cooldown/Throttling, Token-Verbrauch).
- django-axes 9.0.0 (Benutzernamen-Ermittlung, Logging).
- swisstopo/geo.admin.ch: https://docs.geo.admin.ch/visualize-data/xyz.html,
  https://docs.geo.admin.ch/visualize-data/wmts.html (Cache Update),
  https://docs.geo.admin.ch/access-data/search.html (SearchServer),
  WMTS-Capabilities EPSG:3857 (Layer `ch.swisstopo.pixelkarte-farbe`, TileMatrixSet 3857_19),
  Nutzungsbedingungen https://www.geo.admin.ch/en/general-terms-of-use-fsdi (Fair Use, Quellenangabe).
- Leaflet 1.9.4 (npm-Registry, dist-tags: latest 1.9.4; 2.0 nur Alpha/Beta).
- NIST SP 800-63B-4; OWASP Cheat Sheets (E-Mail-Verifikation, MFA, Autorisierung, SSRF, Anti-Automation).
- pip: reproduzierbare/hashgepruefte Installation; Python venv; tarfile-Extraktionsfilter (`filter="data"`).

Hosting, Postfaecher der Initialkonten und Git-Server des Betreibers wurden nicht kontaktiert.
Eine Vorgaengerimplementierung (V0.3/V0.4) existierte in diesem Projekt nicht; docs/MIGRATION_V03_V05.md entfaellt.
