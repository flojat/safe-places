"""Formulare fuer das Betreuerportal. Nur ausdruecklich erlaubte Felder.

Nicht enthalten (und damit nicht ueber zusaetzliche POST-Daten aenderbar):
managed_by, review_state, work_version, editorial_notes, review_feedback,
Freigabe-/Staff-/Rollenfelder.
"""

from __future__ import annotations

from django import forms

from .geo import CoordinateError, to_coordinate, validate_swiss_point
from . import categories
from .models import Location, Photo, Provider

OWNER_FIELDS = [
    "title", "categories", "preview_text", "long_description", "details", "place_text",
    "exact_address", "exact_lat", "exact_lon", "member_exact_location_released",
    "preview_lat", "preview_lon",
    "contact_name", "contact_organisation", "contact_email", "contact_phone", "contact_website",
    "contacts_released", "guest_long_description_released", "guest_details_released", "guest_contacts_released",
    "provider", "internal_notes",
]


def categories_field() -> forms.MultipleChoiceField:
    """Mehrfachauswahl als Checkboxen; gespeichert als normalisierte Liste."""
    return forms.MultipleChoiceField(
        label="Kategorien", choices=categories.choices, required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "category-choices"}),
        help_text="Alle passenden Kategorien anwaehlen (fuer die Freigabe mindestens eine).",
    )


GUEST_RELEASE_HELP = "Standard: aus. Wirksam erst mit der naechsten Freigabe durch die Redaktion."


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
            "guest_long_description_released", "guest_details_released", "guest_contacts_released")}
        widgets = {
            "long_description": forms.Textarea(attrs={"rows": 6}),
            "details": forms.Textarea(attrs={"rows": 3}),
            "internal_notes": forms.Textarea(attrs={"rows": 2}),
            "preview_text": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        # Nur eigene, ausdruecklich zugeordnete Anbieter (keine allgemeine Anbieterliste).
        self.fields["provider"].queryset = Provider.objects.filter(managed_by=user)
        self.fields["provider"].required = False
        for name in ("exact_lat", "exact_lon", "preview_lat", "preview_lon"):
            self.fields[name].widget = forms.TextInput(attrs={"inputmode": "decimal"})

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
