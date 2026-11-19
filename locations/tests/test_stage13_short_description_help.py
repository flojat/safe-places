"""Kurzbeschreibung (Gastfeld zwischen Vorschautext und Langbeschreibung) und durch die Administration
gepflegte Hilfetexte ("i"-Overlay) zu Titel, Vorschautext, Kurzbeschreibung, Langbeschreibung, Details."""

import json
import re

from django.test import Client, TestCase
from django.urls import reverse

from locations.models import AuditEvent, FieldHelp
from locations.roles import EDITOR, OBJECT_MANAGER
from locations.services import publishing

from .test_stage1_guest import make_location, publish_now
from .test_stage4_work import verified

HELP_FIELDS = ["title", "preview_text", "short_description", "long_description", "details"]


def help_data(html: str) -> dict:
    match = re.search(r'<script id="field-help-data" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(match.group(1)) if match else {}


class ShortDescriptionTests(TestCase):
    def setUp(self):
        self.loc = make_location(short_description="KURZ-GAST Rollstuhlgaengig, Garten.")
        self.pub = publish_now(self.loc)
        self.url = reverse("locations:preview", args=[self.pub.public_id])

    def test_snapshot_contains_short_description(self):
        self.assertEqual(self.pub.short_description, "KURZ-GAST Rollstuhlgaengig, Garten.")

    def test_guest_sees_short_description_between_preview_and_long_text(self):
        page = Client().get(self.url).content.decode()
        self.assertIn("KURZ-GAST", page)
        self.assertLess(page.index("Kurzvorschau"), page.index("KURZ-GAST"))
        self.assertNotIn("MITGLIEDERDETAIL", page)

    def test_member_detail_order(self):
        member, _ = verified("m@example.org")
        member.post(reverse("locations:open_details", args=[self.pub.public_id]))
        page = member.get(self.url).content.decode()
        self.assertLess(page.index("Kurzvorschau"), page.index("KURZ-GAST"))
        self.assertLess(page.index("KURZ-GAST"), page.index("MITGLIEDERDETAIL-T"))

    def test_list_api_and_search_use_short_description(self):
        guest = Client()
        guest.get("/")
        r = guest.get(reverse("locations:api_list"), {"q": "Rollstuhlgaengig"}).json()
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["results"][0]["short_description"], "KURZ-GAST Rollstuhlgaengig, Garten.")

    def test_change_requires_new_publication_without_new_disclosure_version(self):
        version = self.pub.disclosure_version
        publishing.save_work_version(self.loc.pk, None, self.loc.work_version,
                                     {"short_description": "KURZ-NEU"})
        self.loc.refresh_from_db()
        self.assertNotIn("KURZ-NEU", Client().get(self.url).content.decode())
        pub = publish_now(self.loc)
        self.assertEqual(pub.short_description, "KURZ-NEU")
        self.assertEqual(pub.disclosure_version, version)  # Gastfeld: keine neue Mitgliederfassung

    def test_owner_can_edit_and_change_is_named_in_notice(self):
        owner_client, owner = verified("o@example.org", groups=[OBJECT_MANAGER])
        self.loc.managed_by = owner
        self.loc.save()
        page = owner_client.get(reverse("portal:edit", args=[self.loc.public_id])).content.decode()
        self.assertIn('name="short_description"', page)
        self.assertLess(page.index('name="preview_text"'), page.index('name="short_description"'))
        self.assertLess(page.index('name="short_description"'), page.index('name="long_description"'))
        values, _ = publishing.snapshot_values(self.loc)
        values["short_description"] = "anders"
        self.assertIn("Kurzbeschreibung", publishing.changed_sections(self.pub, values, []))


class FieldHelpTests(TestCase):
    def setUp(self):
        self.owner_client, self.owner = verified("o@example.org", groups=[OBJECT_MANAGER])
        self.loc = make_location(managed_by=self.owner)
        self.edit_url = reverse("portal:edit", args=[self.loc.public_id])

    def test_default_texts_exist_for_all_five_fields(self):
        self.assertEqual(sorted(FieldHelp.objects.values_list("field", flat=True)), sorted(HELP_FIELDS))
        self.assertFalse(FieldHelp.objects.filter(text="").exists())

    def test_portal_edit_and_create_embed_help_and_script(self):
        for url in (self.edit_url, reverse("portal:create")):
            page = self.owner_client.get(url).content.decode()
            self.assertIn("js/field_help.js", page)
            data = help_data(page)
            self.assertEqual(sorted(data), sorted(HELP_FIELDS))
            self.assertEqual(data["short_description"]["label"], "Kurzbeschreibung")

    def test_editor_sees_help_in_admin_form(self):
        editor, _ = verified("r@example.org", groups=[EDITOR], staff=True)
        page = editor.get(reverse("admin:locations_location_change", args=[self.loc.pk])).content.decode()
        self.assertEqual(sorted(help_data(page)), sorted(HELP_FIELDS))
        self.assertIn('for="id_short_description"', page)

    def test_guests_never_get_help_texts(self):
        pub = publish_now(self.loc)
        page = Client().get(reverse("locations:preview", args=[pub.public_id])).content.decode()
        self.assertNotIn("field-help-data", page)
        self.assertNotIn("field_help.js", page)

    def test_empty_text_hides_icon_and_text_is_escaped(self):
        FieldHelp.objects.filter(field="details").update(text="")
        FieldHelp.objects.filter(field="title").update(text="<script>alert(1)</script>")
        page = self.owner_client.get(self.edit_url).content.decode()
        data = help_data(page)
        self.assertNotIn("details", data)
        self.assertEqual(data["title"]["text"], "<script>alert(1)</script>")
        self.assertNotIn("<script>alert(1)</script>", page)

    def test_only_admin_maintains_help_texts(self):
        entry = FieldHelp.objects.get(field="title")
        url = reverse("admin:locations_fieldhelp_change", args=[entry.pk])
        editor, _ = verified("r@example.org", groups=[EDITOR], staff=True)
        self.assertNotEqual(editor.get(url).status_code, 200)
        editor.post(url, {"text": "geaendert"})
        self.assertNotEqual(FieldHelp.objects.get(pk=entry.pk).text, "geaendert")
        admin, admin_user = verified("a@example.org", superuser=True)
        self.assertEqual(admin.get(url).status_code, 200)
        self.assertEqual(admin.get(reverse("admin:locations_fieldhelp_add")).status_code, 403)
        r = admin.post(url, {"text": "Neuer Hilfetext\n\nZweiter Absatz"})
        self.assertEqual(r.status_code, 302)
        entry.refresh_from_db()
        self.assertIn("Zweiter Absatz", entry.text)
        self.assertEqual(entry.updated_by, admin_user)
        self.assertTrue(AuditEvent.objects.filter(action="field_help_change", target_id="title").exists())
        self.assertIn("Neuer Hilfetext", json.dumps(help_data(self.owner_client.get(self.edit_url).content.decode())))

    def test_editor_edit_notice_names_short_description(self):
        from locations.services import owner_notices

        self.assertEqual(owner_notices.editor_edit_sections(["short_description"], False), ["Kurzbeschreibung"])


class ReleaseToggleLayoutTests(TestCase):
    """Freigabe-Schalter stehen direkt beim zugehoerigen Feld (Portal und Verwaltung)."""

    ORDER = [
        'name="long_description"', 'name="guest_long_description_released"',
        'name="details"', 'name="guest_details_released"',
        'name="contact_website"', 'name="contacts_released"', 'name="guest_contacts_released"',
    ]

    def assert_order(self, page):
        positions = [page.index(marker) for marker in self.ORDER]
        self.assertEqual(positions, sorted(positions))
        # Kein anderes Eingabefeld zwischen Textfeld und zugehoerigem Schalter
        for field, toggle in (("long_description", "guest_long_description_released"),
                              ("details", "guest_details_released")):
            between = page[page.index(f'name="{field}"'):page.index(f'name="{toggle}"')]
            self.assertEqual(between.count('name="'), 1, field)

    def test_portal_places_toggles_below_their_fields_with_clear_labels(self):
        owner_client, owner = verified("o@example.org", groups=[OBJECT_MANAGER])
        loc = make_location(managed_by=owner)
        page = owner_client.get(reverse("portal:edit", args=[loc.public_id])).content.decode()
        self.assert_order(page)
        self.assertEqual(page.count('class="release-toggle"'), 5)
        self.assertIn("Langbeschreibung auch für Besucher (Gäste) sichtbar machen", page)
        self.assertIn("Kontaktangaben für Mitglieder freigeben", page)

    def test_admin_places_toggles_below_their_fields(self):
        editor, _ = verified("r@example.org", groups=[EDITOR], staff=True)
        loc = make_location()
        page = editor.get(reverse("admin:locations_location_change", args=[loc.pk])).content.decode()
        self.assert_order(page)
        self.assertIn("Detailangaben auch für Besucher (Gäste) sichtbar machen", page)

    def test_saving_toggles_still_works(self):
        owner_client, owner = verified("o2@example.org", groups=[OBJECT_MANAGER])
        loc = make_location(managed_by=owner)
        from locations.forms import OWNER_FIELDS, LocationWorkForm

        form = LocationWorkForm(instance=loc, user=owner)
        data = {name: form[name].value() for name in OWNER_FIELDS if form[name].value() is not None}
        data.update(expected_version=loc.work_version, guest_long_description_released="on",
                    categories=["beratung"])
        data = {k: v for k, v in data.items() if v is not False}
        r = owner_client.post(reverse("portal:edit", args=[loc.public_id]), data)
        self.assertEqual(r.status_code, 302)
        loc.refresh_from_db()
        self.assertTrue(loc.guest_long_description_released)
        self.assertFalse(loc.guest_details_released)

    def test_help_texts_explain_toggles(self):
        texts = dict(FieldHelp.objects.values_list("field", "text"))
        self.assertIn("auch für Besucher (Gäste) sichtbar machen", texts["long_description"])
        self.assertIn("auch für Besucher (Gäste) sichtbar machen", texts["details"])
