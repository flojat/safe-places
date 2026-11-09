"""Formulare fuer das Betreuerportal. Nur ausdruecklich erlaubte Felder.

Nicht enthalten (und damit nicht ueber zusaetzliche POST-Daten aenderbar):
managed_by, review_state, work_version, editorial_notes, review_feedback,
Freigabe-/Staff-/Rollenfelder.
"""

from __future__ import annotations

from django import forms

from .geo import CoordinateError, to_coordinate, validate_swiss_point
from .models import Location, Photo, Provider

OWNER_FIELDS = [
    "title", "category", "preview_text", "long_description", "details", "place_text",
    "exact_address", "exact_lat", "exact_lon", "member_exact_location_released",
    "preview_lat", "preview_lon",
    "contact_name", "contact_organisation", "contact_email", "contact_phone", "contact_website",
    "contacts_released", "provider", "internal_notes",
]


class LocationWorkForm(forms.ModelForm):
    expected_version = forms.IntegerField(widget=forms.HiddenInput, min_value=1)

    class Meta:
        model = Location
        fields = OWNER_FIELDS
        labels = {"internal_notes": "Eigene interne Notizen (nie veroeffentlicht)"}
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
        return data

    def changes(self) -> dict:
        return {f: self.cleaned_data[f] for f in OWNER_FIELDS}


class NewLocationForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = ["title", "category"]


class PhotoUploadForm(forms.Form):
    image = forms.FileField(label="Bild (JPEG, PNG oder WebP, max. 10 MiB)")
    alt_text = forms.CharField(label="Alternativtext", max_length=200, required=False)
    visibility = forms.ChoiceField(label="Sichtbarkeit nach Freigabe", choices=Photo.Visibility.choices,
                                   initial=Photo.Visibility.INTERNAL)


class PhotoEditForm(forms.Form):
    alt_text = forms.CharField(label="Alternativtext", max_length=200, required=False)
    visibility = forms.ChoiceField(label="Sichtbarkeit nach Freigabe", choices=Photo.Visibility.choices)
    sort_order = forms.IntegerField(label="Reihenfolge", min_value=0, max_value=999)
