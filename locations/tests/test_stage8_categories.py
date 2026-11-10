"""Mehrere Kategorien je Objekt: Speicherung, Snapshot, Filter (Checkboxen, ODER), Anzeige."""

import re

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from locations import projections
from locations.models import Location, PublishedLocation, normalize_categories
from locations.roles import EDITOR, OBJECT_MANAGER
from locations.services import publishing

from .test_stage1_guest import make_location, publish_now
from .test_stage4_work import verified


def published(title, categories):
    return publish_now(make_location(title=title, categories=categories))


class CategoryModelTests(TestCase):
    def test_normalized_order_unique_and_known_only(self):
        self.assertEqual(normalize_categories(["freizeit", "beratung", "freizeit", "admin", 3]),
                         ["beratung", "freizeit"])
        loc = make_location(categories=["bildung", "beratung", "bildung", "unbekannt"])
        loc.refresh_from_db()
        self.assertEqual(loc.categories, ["beratung", "bildung"])

    def test_publication_needs_at_least_one_category(self):
        loc = make_location(categories=[])
        publishing.submit(loc.pk, None, loc.work_version)
        with self.assertRaisesMessage(publishing.PublishError, "Kategorie"):
            publishing.publish(loc.pk, None, loc.work_version)

    def test_snapshot_keeps_categories_until_next_publication(self):
        pub = published("Snapshot", ["beratung", "freizeit"])
        self.assertEqual(pub.categories, ["beratung", "freizeit"])
        Location.objects.filter(pk=pub.location_id).update(categories=["bildung"])
        pub.refresh_from_db()
        self.assertEqual(pub.categories, ["beratung", "freizeit"])


class CategoryFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.a = published("A Beratung+Bildung", ["beratung", "bildung"])
        cls.b = published("B Freizeit", ["freizeit"])
        cls.c = published("C Gesundheit", ["gesundheit"])

    def setUp(self):
        self.client.get("/")

    def ids(self, **params):
        r = self.client.get(reverse("locations:api_list"), params)
        self.assertEqual(r.status_code, 200)
        return {item["id"] for item in r.json()["results"]}

    def test_several_categories_match_any(self):
        self.assertEqual(self.ids(), {self.a.public_id, self.b.public_id, self.c.public_id})
        self.assertEqual(self.ids(category=["bildung"]), {self.a.public_id})
        self.assertEqual(self.ids(category=["bildung", "freizeit"]), {self.a.public_id, self.b.public_id})
        points = self.client.get(reverse("locations:api_points"), {"category": ["bildung", "freizeit"]}).json()
        self.assertEqual(points["count"], 2)

    def test_unknown_or_too_many_categories_are_rejected(self):
        for value in (["admin"], ["beratung", "x"], ["beratung"] * 7):
            with self.subTest(value=value):
                r = self.client.get(reverse("locations:api_list"), {"category": value})
                self.assertEqual(r.status_code, 400)

    def test_list_returns_all_assigned_categories_with_labels(self):
        r = self.client.get(reverse("locations:api_list"), {"category": ["bildung"]}).json()
        self.assertEqual(r["results"][0]["categories"],
                         [{"value": "beratung", "label": "Beratung", "icon": "sprechblase"},
                          {"value": "bildung", "label": "Bildung", "icon": "buch"}])

    def test_search_by_label_finds_secondary_category(self):
        r = self.client.get(reverse("locations:api_list"), {"q": "Bildung"}).json()
        self.assertEqual({i["id"] for i in r["results"]}, {self.a.public_id})

    def test_home_offers_checkboxes_not_dropdown(self):
        page = self.client.get("/").content.decode()
        self.assertIn('type="checkbox" name="category" value="beratung"', page)
        self.assertNotIn('<select id="category"', page)

    def test_preview_shows_all_categories(self):
        page = self.client.get(reverse("locations:preview", args=[self.a.public_id]))
        self.assertContains(page, '<li class="tag">Beratung</li>', html=False)
        self.assertContains(page, '<li class="tag">Bildung</li>', html=False)

    def test_guest_projection_has_no_internal_category_data(self):
        data = projections.guest_projection(PublishedLocation.objects.get(pk=self.a.pk))
        self.assertEqual(set(data["categories"][0]), {"value", "label", "icon"})


class CategoryFormTests(TestCase):
    def test_owner_can_assign_several_categories_in_portal(self):
        client, user = verified("o@example.org", groups=[OBJECT_MANAGER])
        r = client.post(reverse("portal:create"), {"title": "Mehrfach", "categories": ["freizeit", "bildung"]})
        self.assertEqual(r.status_code, 302)
        loc = Location.objects.get(title="Mehrfach")
        self.assertEqual(loc.categories, ["bildung", "freizeit"])
        self.assertEqual(loc.managed_by, user)
        page = client.get(reverse("portal:edit", args=[loc.public_id])).content.decode()
        self.assertIn('type="checkbox" name="categories" value="freizeit"', page)

    def test_unknown_category_is_rejected_by_form(self):
        client, _user = verified("o@example.org", groups=[OBJECT_MANAGER])
        r = client.post(reverse("portal:create"), {"title": "Falsch", "categories": ["admin"]})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Location.objects.filter(title="Falsch").exists())

    def test_admin_change_form_uses_checkboxes(self):
        client, _user = verified("r@example.org", groups=[EDITOR], staff=True)
        loc = make_location(categories=["beratung"])
        page = client.get(reverse("admin:locations_location_change", args=[loc.pk])).content.decode()
        self.assertIn('type="checkbox" name="categories" value="beratung"', page)
        tag = re.search(r'<input[^>]*name="categories"[^>]*value="beratung"[^>]*>', page).group(0)
        self.assertIn("checked", tag)


class CategoryMigrationTests(TransactionTestCase):
    """Bestehende Einzelkategorie wird in die Liste uebernommen (Objekt und Snapshot)."""

    before = [("locations", "0009_editorial_direct_publish")]
    after = [("locations", "0010_multiple_categories")]

    def test_single_category_is_copied(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        old = executor.loader.project_state(self.before).apps
        OldLocation = old.get_model("locations", "Location")
        OldPublished = old.get_model("locations", "PublishedLocation")
        loc = OldLocation.objects.create(title="Alt", category="bildung")
        empty = OldLocation.objects.create(title="Leer", category="")
        OldPublished.objects.create(location_id=loc.pk, public_id="pubalt0000000001", title="Alt",
                                    category="bildung", preview_text="x", place_text="Bern",
                                    preview_lat="46.9", preview_lon="7.4", disclosure_version=1,
                                    source_work_version=1, published_at=timezone.now())
        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        new = executor.loader.project_state(self.after).apps
        NewLocation = new.get_model("locations", "Location")
        NewPublished = new.get_model("locations", "PublishedLocation")
        self.assertEqual(NewLocation.objects.get(pk=loc.pk).categories, ["bildung"])
        self.assertEqual(NewLocation.objects.get(pk=empty.pk).categories, [])
        self.assertEqual(NewPublished.objects.get(location_id=loc.pk).categories, ["bildung"])
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
