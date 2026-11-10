"""Pflegbare Kategorien (nur Administration) und Einzelfreigaben fuer nicht angemeldete Besucher."""

from django.test import Client, TestCase
from django.urls import reverse

from locations.models import AuditEvent, Category, Location, PublishedLocation
from locations.roles import EDITOR, OBJECT_MANAGER
from locations.services import publishing

from .test_stage1_guest import make_location, publish_now
from .test_stage4_work import edit_post, verified


class CategoryAdminTests(TestCase):
    def setUp(self):
        self.admin_client, self.admin = verified("admin@example.org", superuser=True)

    def add(self, **data):
        payload = {"slug": "natur", "label": "Natur", "icon": "blatt", "sort_order": "5", "is_active": "on"}
        payload.update(data)
        return self.admin_client.post(reverse("admin:locations_category_add"), payload)

    def test_admin_adds_category_which_becomes_selectable_and_filterable(self):
        r = self.add()
        self.assertEqual(r.status_code, 302)
        cat = Category.objects.get(slug="natur")
        self.assertEqual((cat.label, cat.icon), ("Natur", "blatt"))
        self.assertTrue(AuditEvent.objects.filter(action="category_add", target_id="natur").exists())

        guest = Client()
        home = guest.get("/").content.decode()
        self.assertIn('value="natur"', home)
        # Reihenfolge 5: vor den Startkategorien (10, 20, ...)
        self.assertLess(home.index('value="natur"'), home.index('value="beratung"'))

        pub = publish_now(make_location(title="Alp", categories=["natur", "freizeit"]))
        data = guest.get(reverse("locations:api_list"), {"category": ["natur"]}).json()
        self.assertEqual([i["id"] for i in data["results"]], [pub.public_id])
        self.assertEqual(data["results"][0]["categories"][0], {"value": "natur", "label": "Natur", "icon": "blatt"})

        owner_client, _owner = verified("o@example.org", groups=[OBJECT_MANAGER])
        page = owner_client.get(reverse("portal:create")).content.decode()
        self.assertIn('name="categories" value="natur"', page)

    def test_slug_is_fixed_after_creation_and_delete_is_not_offered(self):
        self.add()
        cat = Category.objects.get(slug="natur")
        url = reverse("admin:locations_category_change", args=[cat.pk])
        self.admin_client.post(url, {"slug": "anders", "label": "Natur & Landschaft", "icon": "berg",
                                     "sort_order": "5", "is_active": "on"})
        cat.refresh_from_db()
        self.assertEqual((cat.slug, cat.label, cat.icon), ("natur", "Natur & Landschaft", "berg"))
        r = self.admin_client.post(reverse("admin:locations_category_delete", args=[cat.pk]), {"post": "yes"})
        self.assertNotEqual(r.status_code, 302)
        self.assertTrue(Category.objects.filter(pk=cat.pk).exists())

    def test_invalid_slug_and_duplicate_are_rejected(self):
        self.assertEqual(self.add(slug="Nicht gültig!").status_code, 200)
        self.assertEqual(self.add(slug="beratung").status_code, 200)
        self.assertFalse(Category.objects.filter(label="Natur").exists())

    def test_inactive_category_is_hidden_and_not_selectable(self):
        pub = publish_now(make_location(title="Doppelt", categories=["beratung", "bildung"]))
        Category.objects.filter(slug="bildung").update(is_active=False)
        guest = Client()
        self.assertNotIn('value="bildung"', guest.get("/").content.decode())
        self.assertEqual(guest.get(reverse("locations:api_list"), {"category": ["bildung"]}).status_code, 400)
        item = guest.get(reverse("locations:api_list")).json()["results"][0]
        self.assertEqual(item["id"], pub.public_id)
        self.assertEqual([c["value"] for c in item["categories"]], ["beratung"])

    def test_editor_and_manager_cannot_manage_categories(self):
        editor_client, _ = verified("r@example.org", groups=[EDITOR], staff=True)
        r = editor_client.post(reverse("admin:locations_category_add"),
                               {"slug": "x", "label": "X", "icon": "etikett", "sort_order": "1", "is_active": "on"})
        self.assertIn(r.status_code, (302, 403))
        self.assertFalse(Category.objects.filter(slug="x").exists())
        self.assertNotIn("Kategorien", editor_client.get(reverse("admin:index")).content.decode())
        owner_client, _ = verified("o@example.org", groups=[OBJECT_MANAGER])
        r = owner_client.get(reverse("admin:locations_category_changelist"))
        self.assertNotEqual(r.status_code, 200)


class GuestReleaseTests(TestCase):
    def setUp(self):
        self.owner_client, self.owner = verified("o@example.org", groups=[OBJECT_MANAGER])
        self.loc = make_location(title="Freigabetest", long_description="LANG-TEXT-BESCHREIBUNG",
                                 details="DETAIL-TEXT-ANGABEN", contact_name="KONTAKT-NAME",
                                 contact_email="kontakt@example.org", contacts_released=True,
                                 managed_by=self.owner)
        self.pub = publish_now(self.loc)
        self.loc.refresh_from_db()

    def guest_page(self):
        return Client().get(reverse("locations:preview", args=[self.pub.public_id])).content.decode()

    def owner_sets(self, **flags):
        self.loc.refresh_from_db()
        data = edit_post(self.loc, long_description=self.loc.long_description, details=self.loc.details,
                         contact_name=self.loc.contact_name, contact_email=self.loc.contact_email,
                         contacts_released="on")
        data.update({k: "on" for k, v in flags.items() if v})
        r = self.owner_client.post(reverse("portal:edit", args=[self.loc.public_id]), data)
        self.loc.refresh_from_db()
        return r

    def submit_and_publish(self):
        self.loc.refresh_from_db()
        publishing.submit(self.loc.pk, self.owner, self.loc.work_version)
        publishing.publish(self.loc.pk, None, self.loc.work_version)

    def test_default_is_not_released(self):
        page = self.guest_page()
        for marker in ("LANG-TEXT-BESCHREIBUNG", "DETAIL-TEXT-ANGABEN", "KONTAKT-NAME", "kontakt@example.org"):
            self.assertNotIn(marker, page)
        self.assertFalse(Location._meta.get_field("guest_long_description_released").default)

    def test_each_field_is_released_individually_after_publication(self):
        self.owner_sets(guest_long_description_released=True)
        self.assertTrue(self.loc.guest_long_description_released)
        self.assertNotIn("LANG-TEXT-BESCHREIBUNG", self.guest_page())  # erst nach Freigabe
        self.submit_and_publish()
        page = self.guest_page()
        self.assertIn("LANG-TEXT-BESCHREIBUNG", page)
        self.assertNotIn("DETAIL-TEXT-ANGABEN", page)
        self.assertNotIn("KONTAKT-NAME", page)

        self.owner_sets(guest_details_released=True)
        self.submit_and_publish()
        page = self.guest_page()
        self.assertIn("DETAIL-TEXT-ANGABEN", page)
        self.assertNotIn("LANG-TEXT-BESCHREIBUNG", page)
        self.assertNotIn("KONTAKT-NAME", page)

        self.owner_sets(guest_contacts_released=True)
        self.submit_and_publish()
        page = self.guest_page()
        self.assertIn("KONTAKT-NAME", page)
        self.assertIn("kontakt@example.org", page)
        self.assertNotIn("LANG-TEXT-BESCHREIBUNG", page)

    def test_list_api_never_contains_released_long_texts(self):
        self.owner_sets(guest_long_description_released=True, guest_details_released=True,
                        guest_contacts_released=True)
        self.submit_and_publish()
        guest = Client()
        guest.get("/")
        body = guest.get(reverse("locations:api_list")).content.decode()
        for marker in ("LANG-TEXT-BESCHREIBUNG", "DETAIL-TEXT-ANGABEN", "KONTAKT-NAME"):
            self.assertNotIn(marker, body)

    def test_guest_contacts_need_member_contact_release(self):
        self.loc.refresh_from_db()
        data = edit_post(self.loc, contact_name="KONTAKT-NAME", guest_contacts_released="on")
        r = self.owner_client.post(reverse("portal:edit", args=[self.loc.public_id]), data)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "nur zusammen mit der Freigabe fuer Mitglieder")
        self.loc.refresh_from_db()
        self.assertFalse(self.loc.guest_contacts_released)

    def test_contact_withdrawal_removes_guest_contacts_immediately(self):
        self.owner_sets(guest_contacts_released=True)
        self.submit_and_publish()
        self.assertIn("KONTAKT-NAME", self.guest_page())
        self.owner_client.post(reverse("portal:withdraw_contacts", args=[self.loc.public_id]))
        self.assertNotIn("KONTAKT-NAME", self.guest_page())
        pub = PublishedLocation.objects.get(pk=self.pub.pk)
        self.assertFalse(pub.guest_contacts_released)
        self.loc.refresh_from_db()
        self.assertFalse(self.loc.guest_contacts_released)

    def test_editor_can_set_release_in_admin(self):
        from .test_stage7_editorial import admin_form_data

        editor_client, _ = verified("r@example.org", groups=[EDITOR], staff=True)
        url = reverse("admin:locations_location_change", args=[self.loc.pk])
        page = editor_client.get(url)
        self.assertContains(page, "guest_details_released")
        r = editor_client.post(url, admin_form_data(page, guest_details_released=True))
        self.assertEqual(r.status_code, 302)
        self.loc.refresh_from_db()
        self.assertTrue(self.loc.guest_details_released)
