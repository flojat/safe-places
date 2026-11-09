# Abhaengigkeiten

- Direkt: `requirements.in` (Laufzeit), `requirements-dev.in` (Tests: Playwright). Gesperrt mit SHA-256 in
  `requirements.txt`/`requirements-dev.txt` (`pip-compile --generate-hashes --allow-unsafe --strip-extras`).
- Installation immer `pip install --require-hashes`; der Installer veraendert Lockdateien nie und loest nie neu auf.
- Aktualisieren nur in Entwicklung: `.in` anpassen, neu kompilieren, volle Tests, Commit.
- Native Abhaengigkeit: mysqlclient braucht libmariadb (Laufzeit) und ggf. Compiler/Header/pkg-config (Build).
  Lokal aus Quelle gebaut (Ubuntu 24.04, x86_64). Fuer das Hosting: passendes Wheel auf kompatibler Plattform
  bauen und eigene Hashes festhalten oder auf dem Ziel bauen lassen; Source- und Wheel-Hash nicht verwechseln.
- Wheelhouse (Offline): `pip download --require-hashes -r requirements.txt -d wheelhouse/` auf gleicher
  Plattform/Python; dann `install --wheelhouse wheelhouse`.
- Browserassets: Leaflet 1.9.4 (BSD-2-Clause, Lizenz in `static/vendor/leaflet/LICENSE`), Quelle npm-Registry,
  Integritaet laut Registry geprueft; Datei-Hashes in `vendor-manifest.json`; `.gitattributes` haelt Bytes stabil.
- Playwright/Chromium nur Entwicklung (`.playwright-browsers/`, nicht in Git, nicht auf das Hosting).
