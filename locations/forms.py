"""Formulare fuer das Betreuerportal. Nur ausdruecklich erlaubte Felder.

Nicht enthalten (und damit nicht ueber zusaetzliche POST-Daten aenderbar):
managed_by, review_state, work_version, editorial_notes, review_feedback,
Freigabe-/Staff-/Rollenfelder.
"""

from __future__ import annotations

from django import forms

from .geo import CoordinateError, to_coordinate, validate_swiss_point
from . import categories
from .models import EXPERIENCE_REPORT_MAX_CHARS, Location, Photo, Provider

# Reihenfolge = Anzeige: Freigabe-Schalter stehen direkt beim zugehoerigen Textfeld.
OWNER_FIELDS = [
    "title", "categories", "preview_text", "short_description",
    "long_description", "guest_long_description_released",
    "details", "guest_details_released",
    "place_text", "exact_address", "exact_lat", "exact_lon", "member_exact_location_released",
    "preview_lat", "preview_lon",
    "contact_name", "contact_organisation", "contact_email", "contact_phone", "contact_website",
    "contacts_released", "guest_contacts_released",
    "provider", "internal_notes",
]

# Freigabe-Schalter: Beschriftung und Darstellung direkt unter dem zugehoerigen Feld.
RELEASE_LABELS = {
    "guest_long_description_released": "Langbeschreibung auch für Besucher (Gäste) sichtbar machen",
    "guest_details_released": "Detailangaben auch für Besucher (Gäste) sichtbar machen",
    "contacts_released": "Kontaktangaben für Mitglieder freigeben",
    "guest_contacts_released": "Kontaktangaben auch für Besucher (Gäste) sichtbar machen",
    "member_exact_location_released": "Genaue Lage für Mitglieder freigeben",
}
RELEASE_TEMPLATE = "locations/forms/release_checkbox.html"


def apply_release_labels(form) -> None:
    for name, label in RELEASE_LABELS.items():
        if name in form.fields:
            form.fields[name].label = label
            form.fields[name].template_name = RELEASE_TEMPLATE


def categories_field() -> forms.MultipleChoiceField:
    """Mehrfachauswahl als Checkboxen; gespeichert als normalisierte Liste."""
    return forms.MultipleChoiceField(
        label="Kategorien", choices=categories.choices, required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "category-choices"}),
        help_text="Alle passenden Kategorien anwaehlen (fuer die Freigabe mindestens eine).",
    )


GUEST_RELEASE_HELP = "Standard: aus. Wirksam erst mit der nächsten Freigabe durch die Redaktion."


def validate_guest_releases(form, data) -> None:
    """Besucherfreigabe von Kontakten nur zusammen mit der Mitgliederfreigabe."""
    if data.get("guest_contacts_released") and not data.get("contacts_released"):
        form.add_error("guest_contacts_released",
                       "Kontaktangaben fuer Besucher nur zusammen mit der Freigabe fuer Mitglieder.")


class LocationWorkForm(forms.ModelForm):
    expected_version = forms.IntegerField(widget=forms.HiddenInput, min_value=1)
    categories = categories_field()

    class Meta:
        model = Location
        fields = OWNER_FIELDS
        labels = {"internal_notes": "Eigene interne Notizen (nie veroeffentlicht)"}
        help_texts = {f: GUEST_RELEASE_HELP for f in (
            "guest_long_description_released", "guest_details_released", "guest_contacts_released",
            "member_exact_location_released", "contacts_released")}
        widgets = {
            "long_description": forms.Textarea(attrs={"rows": 6}),
            "details": forms.Textarea(attrs={"rows": 3}),
            "internal_notes": forms.Textarea(attrs={"rows": 2}),
            "preview_text": forms.Textarea(attrs={"rows": 2}),
            "short_description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        # Nur eigene, ausdruecklich zugeordnete Anbieter (keine allgemeine Anbieterliste).
        self.fields["provider"].queryset = Provider.objects.filter(managed_by=user)
        self.fields["provider"].required = False
        for name in ("exact_lat", "exact_lon", "preview_lat", "preview_lon"):
            self.fields[name].widget = forms.TextInput(attrs={"inputmode": "decimal"})
        apply_release_labels(self)

    def _pair(self, lat_field: str, lon_field: str, swiss: bool) -> None:
        lat, lon = self.cleaned_data.get(lat_field), self.cleaned_data.get(lon_field)
        if lat is None and lon is None:
            return
        if lat is None or lon is None:
            self.add_error(lat_field, "Breite und Laenge nur gemeinsam angeben.")
            return
        try:
            la, lo = validate_swiss_point(lat, lon) if swiss else (to_coordinate(lat, "lat"), to_coordinate(lon, "lon"))
        except CoordinateError as exc:
            self.add_error(lat_field, str(exc))
            return
        self.cleaned_data[lat_field], self.cleaned_data[lon_field] = la, lo

    def clean(self):
        data = super().clean()
        self._pair("exact_lat", "exact_lon", swiss=True)
        self._pair("preview_lat", "preview_lon", swiss=True)
        if data.get("member_exact_location_released") and data.get("exact_lat") is None:
            self.add_error("member_exact_location_released", "Ohne genaue Lage nicht freigebbar.")
        validate_guest_releases(self, data)
        return data

    def changes(self) -> dict:
        return {f: self.cleaned_data[f] for f in OWNER_FIELDS}


class NewLocationForm(forms.ModelForm):
    categories = categories_field()

    class Meta:
        model = Location
        fields = ["title", "categories"]


PHOTO_DESCRIPTION_HELP = "Wird in der Galerie unter dem Bild angezeigt und als Alternativtext fuer Screenreader verwendet."
RIGHTS_LABEL = ("Ich bestaetige: Ich habe das Bild selbst erstellt oder besitze nachweislich die Nutzungsrechte "
                "(Copyright) fuer die Veroeffentlichung auf der Adresskarte.")


class PhotoUploadForm(forms.Form):
    image = forms.FileField(label="Bild (JPEG, PNG oder WebP, max. 10 MiB)")
    alt_text = forms.CharField(label="Bildbeschreibung", max_length=200, required=False,
                               help_text=PHOTO_DESCRIPTION_HELP)
    visibility = forms.ChoiceField(label="Sichtbarkeit nach Freigabe", choices=Photo.Visibility.choices,
                                   initial=Photo.Visibility.INTERNAL)
    rights_confirmed = forms.BooleanField(
        label=RIGHTS_LABEL, required=True,
        error_messages={"required": "Ohne Bestaetigung der Bildrechte ist kein Hochladen moeglich."},
    )


class PhotoEditForm(forms.Form):
    alt_text = forms.CharField(label="Bildbeschreibung", max_length=200, required=False)
    visibility = forms.ChoiceField(label="Sichtbarkeit nach Freigabe", choices=Photo.Visibility.choices)
    sort_order = forms.IntegerField(label="Reihenfolge", min_value=0, max_value=999)


# --- Erfahrungsberichte (Mitglieder) -------------------------------------------

REPORT_CONSENT_LABEL = (
    "Mit dem Hochladen der Fotos erteile ich ausdrücklich die Berechtigung, diese Fotos ausschliesslich auf "
    "dieser Webseite (Adresskarte) zu veröffentlichen. Ich bestätige, dass ich die Fotos selbst erstellt habe "
    "oder die nötigen Rechte besitze und damit keine fremden Bildrechte verletze."
)
REPORT_PHOTO_SLOTS = [
    # (Feldname, Fotoart, Beschriftung, Hinweis)
    ("photo_overview", "overview", "Foto 1: Überblick (Weitsicht)",
     "Vorschlag: eine Aufnahme mit etwas Abstand, die den Ort als Ganzes zeigt."),
    ("photo_detail_1", "detail", "Foto 2: Detail",
     "Vorschlag: ein Detail, das Ihren Bericht wiedergibt oder etwas darin Beschriebenes zeigt."),
    ("photo_detail_2", "detail", "Foto 3: Detail",
     "Vorschlag: ein weiteres Detail zu Ihrem Bericht."),
]


_MAX = EXPERIENCE_REPORT_MAX_CHARS


class ExperienceReportForm(forms.Form):
    text = forms.CharField(
        # Laenge erst nach Normalisierung der Zeilenumbrueche pruefen (Browser senden CRLF): clean_text.
        label="Ihr Erfahrungsbericht",
        widget=forms.Textarea(attrs={"rows": 10, "maxlength": _MAX, "data-char-counter": ""}),
        help_text=f"Höchstens {_MAX} Zeichen. Bitte keine Namen oder Kontaktdaten von Personen nennen.",
    )
    photo_consent = forms.BooleanField(label=REPORT_CONSENT_LABEL, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, _kind, label, hint in REPORT_PHOTO_SLOTS:
            self.fields[name] = forms.FileField(
                label=label, required=False, help_text=hint + " JPEG, PNG oder WebP, max. 10 MiB.",
                widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}))
            self.fields[f"{name}_alt"] = forms.CharField(label="Bildbeschreibung", max_length=200, required=False,
                                                         help_text=PHOTO_DESCRIPTION_HELP)
        # Reihenfolge: Text, je Foto Datei + Beschreibung, dann Bestaetigung.
        order = ["text"] + [n for slot in REPORT_PHOTO_SLOTS for n in (slot[0], f"{slot[0]}_alt")] + ["photo_consent"]
        self.order_fields(order)

    def clean_text(self):
        from .services.experience_reports import ReportRejected, validate_text

        try:
            return validate_text(self.cleaned_data["text"])
        except ReportRejected as exc:
            raise forms.ValidationError(str(exc)) from exc

    def clean(self):
        data = super().clean()
        if self.photo_inputs() and not data.get("photo_consent"):
            self.add_error("photo_consent", "Für das Hochladen von Fotos ist diese Bestätigung erforderlich.")
        return data

    def photo_inputs(self):
        from .services.experience_reports import PhotoInput

        out = []
        for name, kind, _label, _hint in REPORT_PHOTO_SLOTS:
            upload = self.cleaned_data.get(name) if hasattr(self, "cleaned_data") else None
            if upload:
                out.append(PhotoInput(kind=kind, upload=upload, alt_text=self.cleaned_data.get(f"{name}_alt", "")))
        return out
