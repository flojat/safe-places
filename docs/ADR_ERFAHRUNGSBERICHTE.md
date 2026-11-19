# ADR: Erfahrungsberichte und (künftige) Bewertungen

Stand: 2026-11-19 · Status: Erfahrungsberichte umgesetzt, Bewertungen nur vorgesehen (nicht umgesetzt).

## Kontext

Mitglieder sollen zu Objekten Erfahrungsberichte erfassen können. Später sollen sie Objekte auch
bewerten bzw. rezensieren können; das ist **komplett getrennt** von den Erfahrungsberichten.
Der ursprüngliche Auftrag V0.5 (Abschnitt 14) schloss «Bewertungen» ausdrücklich aus. Die
Erfahrungsberichte sind eine Erweiterung durch den Auftraggeber (2026-11-19). Für Bewertungen braucht
es vor der Umsetzung einen eigenen Entscheid.

## Entscheid Erfahrungsberichte

| Punkt | Festlegung |
|---|---|
| Modell | `ExperienceReport` (an `Location` gebunden) + `ExperienceReportPhoto` (max. 3) |
| Erfassen | vollständig verifizierte Konten (Mitglieder, Betreuung, Redaktion), nur zu aktiv veröffentlichten Objekten, Route `/objekt/<id>/erfahrungsbericht/`; bucht kein Kontingent, zeigt nur Gastfelder |
| Text | max. 2000 Zeichen nach Normalisierung CRLF→LF (Formular, Dienst, DB-Constraint `CHAR_LENGTH`) |
| Fotos | optional; Vorschlag 1× Überblick (Weitsicht), 2× Detail; gleiche Bildprüfung wie Objektfotos (`services/images.py`) |
| Fotorechte | Pflichtbestätigung: Publikationsberechtigung ausschliesslich für diese Webseite + keine fremden Bildrechte; gespeichert als `photo_consent_at` bzw. `rights_confirmed_at` |
| Zustände | eingereicht → veröffentlicht ⇄ zurückgezogen; eingereicht → zurückgewiesen (endgültig, Fotodateien gelöscht) |
| Freigabe | nur Redaktion/Administration (`can_publish`), Einzelentscheid in der Verwaltung, Textkorrektur und Entfernen einzelner Fotos möglich (protokolliert) |
| Mail an Redaktion | nur wenn ein Konto ohne Redaktionsrolle erfasst; an alle aktiven, bestätigten Redaktionskonten (ohne Redaktion: Administration); ohne Links und ohne Berichtsinhalt; Budget `report_mail_*` |
| Mail an Betreuung | bei der **ersten** Freigabe als `LocationNotice` `report_published` (Portal + Mail, Budget `notice_*`) |
| Sichtbarkeit | nur Mitglieder mit gültiger Freigabebuchung des aktiv veröffentlichten Objekts; nie Gäste; keine Autorenangaben; Fotozugriff wird bei jedem Abruf neu geprüft (`/berichtsmedien/...`) |
| Limits | `report_create_account` 3/h, 10/Tag; `report_create_ip` 10/h, 40/Tag |
| Webserver | `LimitRequestBody` auf 33 MiB angehoben (3 × 10 MiB + Formular) |

## Vorgesehen für Bewertungen/Rezensionen (nicht umgesetzt)

Bewusst **keine** gemeinsame Basistabelle und kein generisches «Beitrag»-Modell, damit sich die beiden
Funktionen unabhängig entwickeln, freigeben und abschalten lassen:

- Eigene Modelle, z. B. `Rating` (Objekt, Konto, Punktzahl, optional `RatingCategory` für Teilaspekte)
  und eventuell `RatingText` für eine Rezension. Constraint: höchstens eine aktive Bewertung je Konto und
  Objekt (bei Erfahrungsberichten bewusst nicht beschränkt).
- Eigener Dienst `services/ratings.py`, eigene Routen, eigene Limits (`rating_*`), eigene Prüfansicht in
  der Verwaltung, eigener Mitteilungstyp.
- Mitbenutzt werden nur Querschnittsbausteine: Rollen/Policies (`permissions.py`, `roles.py`), Limiter,
  Mailversand (`security_mail.deliver`), Bildprüfung, `owner_notices`, Audit.
- Auswertung (z. B. Durchschnitt) nur aus freigegebenen Bewertungen. Ob Gäste Durchschnittswerte sehen,
  ist bewusst offen.
- Offene Fachfragen vor der Umsetzung: Skala, Teilaspekte (gebunden an Objektkategorien?), Moderation
  vorab oder nachträglich, Mindestanzahl vor Anzeige des Durchschnitts, Antwortrecht der Betreuung.
