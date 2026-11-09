# Rollen und Zugriffsgruppen

Eine Benutzerbasis (`locations.User`, E-Mail-Login) mit fuenf Zugriffsgruppen. Rollen sind
kombinierbar. Jede Pruefung verlangt bei jeder Anfrage: aktives Konto, bestaetigte E-Mail,
zweiter Schritt in der aktuellen Sitzung, aktuelle Rolle und ggf. aktuelle Objektzuordnung.

| Gruppe | Wie erlangt | Darf | Darf nicht |
|---|---|---|---|
| Gast | – | Vorschau (Karte, Liste, Vorschauseiten, Gastvorschaubilder) | Details, Kontakte, Mitgliedsfotos, Entwuerfe |
| Mitglied | Registrierung + E-Mail + zweiter Schritt | Details/Kontakte/Fotos/genaue Lage freigegebener Objekte im Kontingent | Entwuerfe, interne Daten, Bearbeitung |
| Objektbetreuung | Rolle durch Administration (oder Initialkonto) | eigene/zugewiesene Objekte erfassen, bearbeiten, Fotos, einreichen, sofort zurueckziehen | veroeffentlichen, fremde Objekte, Admin, Rollen |
| Redaktion | Rolle (Staff + fachliche Rechte) | alle Objekte pruefen, vergleichen, freigeben/zurueckweisen/zurueckziehen, Zuweisung an bestehende Betreuer | Konten, Gruppen, OTP-Geraete, Sicherheitseinstellungen |
| Administration | Superuser (nur Initialkonto oder Vergabe durch Admin) | alles Fachliche, Rollen, Sperren, Kontowiederherstellung | Passwoerter/Secrets sehen, MFA/Budgets umgehen, Systembefehle |

- Registrierung ergibt nur ein Besucherkonto. Keine Rolle aus Mail-Domain, gleicher Adresse oder Objekt-ID.
- `Location.managed_by`: hoechstens ein Betreuungskonto je Objekt (Bearbeitungsbefugnis, kein Eigentumsnachweis);
  Kontoloeschung loescht keine Objekte. `Provider.managed_by` getrennt.
- Rollenvergabe/-entzug und Sperren: nur Administration, mit frischer Bestaetigung, protokolliert;
  beendet bestehende Sitzungen der betroffenen Person. Entzug wirkt ab der naechsten Anfrage.
- Eigenfreigabe (Betreuer mit Redaktionsrolle) ist erlaubt, wird aber protokolliert (`self_publish`).
- Interne Arbeit verbraucht kein Besucherkontingent; auf Besucherseiten gelten fuer alle Rollen die Mitgliedsregeln.
