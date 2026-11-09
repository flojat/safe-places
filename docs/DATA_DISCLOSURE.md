# Datenfreigabe

Drei Projektionen (`locations/projections.py`); nie wird ein vollstaendiges Modell serialisiert.

| Stufe | Inhalte | Quelle |
|---|---|---|
| Gast | ID, Titel, Kategorie, Vorschautext, Ort/Region, grobe Vorschaulage, optional Gastvorschaubild (400 px) | `PublishedLocation` (aktiv) |
| Mitglied | zusaetzlich Langbeschreibung, Details, freigegebene Kontakte, freigegebene genaue Lage/Adresse, Mitgliedsfotos | `PublishedLocation` + gueltige Buchung |
| Verwaltung | Arbeitsfassung, interne Notizen, Entwurfsfotos (rollen-/zuordnungsabhaengig) | `Location` |

- **Lage**: Gaeste erhalten eine stabile Rasterzellenmitte (ca. 2 km) oder eine ausdruecklich gesetzte
  Vorschaulage; keine Zufallsverschiebung. Genaue Lage nur fuer Mitglieder und nur bei ausdruecklicher Freigabe,
  nur in der Einzelansicht (keine praezise Mitgliederkarte). Auch grobe Lagen garantieren keine Anonymitaet.
- **Kontakte**: objektbezogene Felder mit eigener Freigabe; nie automatisch aus Anbieter- oder Kontodaten.
- **Freigegebener Stand**: Redaktion uebernimmt die eingereichte Fassung atomar. Aenderungen danach bleiben privat
  bis zur naechsten Freigabe. `disclosure_version` steigt bei Aenderung von Mitgliedsfeldern/-fotos.
- **Buchung** (`DisclosureGrant`): Konto + Objekt + `disclosure_version`, 24 h ab erster Buchung, keine
  Verlaengerung; nur per POST; vor jeder Ausgabe werden Konto, MFA, Veroeffentlichung und Buchung erneut geprueft.
  Rueckzug von Objekt, Kontakten oder Fotos wirkt sofort; kein Quoten-Refund.
- **Fotos**: Originale verworfen; Varianten privat, Auslieferung nur ueber `/medien/...` mit Policy, `no-store`.
- **Caching**: Mitglieds-/Verwaltungsantworten `private, no-store`; Logout leert den Browsercache.
- **Grenze**: Bereits gesehene, kopierte oder fotografierte Inhalte lassen sich nicht zurueckholen.
