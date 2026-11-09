"""Etappe 1: Datenmodell, Projektionen, Freigabe, Gast-Endpunkte, Kachelweg, seed_demo."""

import json
import math
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from locations import projections
from locations.geo import (
    CoordinateError, coarse_preview_point, parse_bbox, to_coordinate, validate_swiss_point,
)
from locations.models import Location, PublishedLocation
from locations.services import publishing

# Funktionstests der Gastansicht mit grosszuegigen Limits; die Limits selbst testet test_stage3.
RELAXED_LIMITS = override_settings(RATE_LIMITS={
    **__import__("django.conf", fromlist=["settings"]).settings.RATE_LIMITS,
    "query_guest_session": [(1000, 60)], "query_ip": [(1000, 60)],
})

FORBIDDEN_IN_GUEST = ("CANARY-", "MITGLIEDERDETAIL-", "@example.org", "+41 00 000", "example.org/demo")


def make_location(**overrides) -> Location:
    data = dict(
        title="Testobjekt", category="beratung", preview_text="Kurzvorschau",
        long_description="MITGLIEDERDETAIL-T lang", place_text="Bern",
        exact_lat=Decimal("46.948000"), exact_lon=Decimal("7.447400"),
        exact_address="CANARY-ADRESSE", internal_notes="CANARY-INTERN",
        editorial_notes="CANARY-REDAKTION",
        contact_name="Frau Test", contact_email="t@example.org", contacts_released=True,
    )
    data.update(overrides)
    return Location.objects.create(**data)


def publish_now(loc: Location) -> PublishedLocation:
    publishing.submit(loc.pk, None, loc.work_version)
    return publishing.publish(loc.pk, None, loc.work_version)


class GeoTests(TestCase):
    def test_rejects_non_finite_and_out_of_range(self):
        for bad in ("nan", "NaN", "inf", "-Infinity", float("nan"), float("inf"), "", None, "1e400", "abc", True):
            with self.subTest(bad=bad), self.assertRaises(CoordinateError):
                to_coordinate(bad, "lat")
        with self.assertRaises(CoordinateError):
            to_coordinate("90.1", "lat")
        with self.assertRaises(CoordinateError):
            to_coordinate("-180.5", "lon")

    def test_swiss_plausibility_rectangle(self):
        validate_swiss_point("46.95", "7.44")
        for lat, lon in (("48.5", "7.4"), ("46.9", "12.0"), ("7.44", "46.95")):  # inkl. vertauscht
            with self.subTest(lat=lat, lon=lon), self.assertRaises(CoordinateError):
                validate_swiss_point(lat, lon)

    def test_coarse_point_is_stable_and_grid_based(self):
        a = coarse_preview_point("46.948012", "7.447401")
        b = coarse_preview_point("46.948012", "7.447401")
        c = coarse_preview_point("46.9481", "7.4475")  # gleiche Zelle
        self.assertEqual(a, b)
        self.assertEqual(a, c)
        self.assertNotEqual(a, (Decimal("46.948012"), Decimal("7.447401")))

    def test_bbox_validation(self):
        self.assertIsNone(parse_bbox(None))
        box = parse_bbox("7.0,46.5,8.0,47.5")
        self.assertEqual((box.west, box.north), (Decimal("7.000000"), Decimal("47.500000")))
        for bad in ("1,2,3", "8,46,7,47", "7,47,8,46", "nan,46,8,47", "7,46,inf,47",
                    "100,10,120,20", "7,46,8,47,9", "x" * 101):
            with self.subTest(bad=bad), self.assertRaises(CoordinateError):
                parse_bbox(bad)

    def test_db_constraints_reject_half_pairs_and_ranges(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Location.objects.create(title="x", exact_lat=Decimal("46.9"), exact_lon=None)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Location.objects.create(title="x", preview_lat=Decimal("95"), preview_lon=Decimal("7"))


class PublishingTests(TestCase):
    def test_draft_and_submitted_are_invisible(self):
        loc = make_location()
        self.assertFalse(projections.visible_published().exists())
        publishing.submit(loc.pk, None, loc.work_version)
        self.assertFalse(projections.visible_published().exists())

    def test_publish_requires_submission_and_matching_version(self):
        loc = make_location()
        with self.assertRaises(publishing.PublishError):
            publishing.publish(loc.pk, None, loc.work_version)
        publishing.submit(loc.pk, None, loc.work_version)
        with self.assertRaises(publishing.VersionConflict):
            publishing.publish(loc.pk, None, loc.work_version + 1)

    def test_incomplete_is_rejected(self):
        loc = make_location(long_description="", exact_lat=None, exact_lon=None)
        publishing.submit(loc.pk, None, loc.work_version)
        with self.assertRaisesMessage(publishing.PublishError, "Langbeschreibung"):
            publishing.publish(loc.pk, None, loc.work_version)

    def test_released_flags_need_data(self):
        loc = make_location(contact_name="", contact_email="", contacts_released=True,
                            member_exact_location_released=True, exact_lat=None, exact_lon=None,
                            preview_lat=Decimal("46.95"), preview_lon=Decimal("7.45"))
        missing = publishing.missing_for_publication(loc)
        self.assertIn("Kontaktangaben (als freigegeben markiert)", missing)
        self.assertIn("genaue Lage (als freigegeben markiert)", missing)

    def test_snapshot_excludes_internal_and_unreleased_fields(self):
        pub = publish_now(make_location(contacts_released=False))
        self.assertEqual(pub.contact_name, "")
        self.assertEqual(pub.contact_email, "")
        self.assertIsNone(pub.member_lat)
        self.assertEqual(pub.member_address, "")
        dumped = json.dumps({f.name: str(getattr(pub, f.name)) for f in pub._meta.concrete_fields})
        self.assertNotIn("CANARY", dumped)
        # Ohne explizite Vorschaulage: grobe Rasterlage statt genauer Lage.
        self.assertNotEqual((pub.preview_lat, pub.preview_lon), (Decimal("46.948000"), Decimal("7.447400")))

    def test_work_edits_do_not_change_published_until_new_approval(self):
        loc = make_location()
        pub = publish_now(loc)
        Location.objects.filter(pk=loc.pk).update(
            title="Geaendert", contact_email="neu@example.org", work_version=2, review_state="draft"
        )
        pub.refresh_from_db()
        self.assertEqual(pub.title, "Testobjekt")
        self.assertEqual(pub.contact_email, "t@example.org")

    def test_disclosure_version_bumps_only_on_member_changes(self):
        loc = make_location()
        pub = publish_now(loc)
        self.assertEqual(pub.disclosure_version, 1)
        Location.objects.filter(pk=loc.pk).update(title="Neuer Titel", work_version=2)
        loc.refresh_from_db()
        pub = publish_now(loc)
        self.assertEqual(pub.disclosure_version, 1)  # nur Gastfeld geaendert
        Location.objects.filter(pk=loc.pk).update(contact_phone="+41 00 000 9999", work_version=3)
        loc.refresh_from_db()
        pub = publish_now(loc)
        self.assertEqual(pub.disclosure_version, 2)

    def test_withdraw_is_immediate(self):
        loc = make_location()
        publish_now(loc)
        self.assertEqual(projections.visible_published().count(), 1)
        publishing.withdraw(loc.pk, None)
        self.assertEqual(projections.visible_published().count(), 0)
        self.assertEqual(self.client.get(reverse("locations:preview", args=[loc.public_id])).status_code, 404)


class ProjectionTests(TestCase):
    def test_guest_projection_has_only_allowed_keys(self):
        pub = publish_now(make_location())
        self.assertEqual(
            set(projections.guest_projection(pub)),
            {"id", "title", "category", "category_label", "preview_text", "place", "lat", "lon"},
        )
        self.assertEqual(set(projections.guest_point(pub)), {"id", "title", "category", "lat", "lon"})

    def test_member_projection_contains_released_details(self):
        pub = publish_now(make_location(member_exact_location_released=True, exact_address="Demoweg 1"))
        data = projections.member_projection(pub)
        self.assertEqual(data["contact"]["email"], "t@example.org")
        self.assertEqual(data["exact_location"]["address"], "Demoweg 1")
        self.assertIn("MITGLIEDERDETAIL-T", data["long_description"])


@RELAXED_LIMITS
class SeededGuestApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", "--count", "100", stdout=StringIO())

    def setUp(self):
        self.client.get("/")  # Gastsitzung wie im Browser

    def test_seed_counts(self):
        self.assertEqual(Location.objects.filter(is_demo=True).count(), 100)
        self.assertEqual(PublishedLocation.objects.filter(is_active=True).count(), 90)
        self.assertEqual(Location.objects.filter(review_state="draft").count(), 10)

    def test_seed_is_idempotent_and_keeps_manual_edits(self):
        Location.objects.filter(external_id="demo-001").update(title="Manuell geaendert")
        out = StringIO()
        call_command("seed_demo", "--count", "100", stdout=out)
        self.assertIn("0 neu", out.getvalue())
        self.assertEqual(Location.objects.get(external_id="demo-001").title, "Manuell geaendert")
        self.assertEqual(Location.objects.count(), 100)

    @override_settings(ENVIRONMENT="production")
    def test_seed_refused_in_production(self):
        with self.assertRaises(CommandError):
            call_command("seed_demo", stdout=StringIO())

    def _get(self, name, **params):
        return self.client.get(reverse(f"locations:{name}"), params)

    def test_points_return_all_published_guest_points(self):
        r = self._get("api_points")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["count"], 90)
        self.assertEqual(len(data["points"]), 90)
        self.assertEqual(r["Cache-Control"], "private, no-store")

    def test_list_is_paged_to_guest_size_but_points_are_not(self):
        data = self._get("api_list").json()
        self.assertEqual(data["count"], 90)
        self.assertEqual(len(data["results"]), 10)
        self.assertEqual(data["pages"], 9)
        last = self._get("api_list", page=9).json()
        self.assertEqual(len(last["results"]), 10)

    def test_list_and_points_use_same_filter(self):
        params = {"bbox": "8.40,47.30,8.70,47.45", "category": "beratung"}
        pts = self._get("api_points", **params).json()
        lst = self._get("api_list", **params).json()
        self.assertEqual(pts["count"], lst["count"])
        self.assertEqual({p["id"] for p in pts["points"]} >= {r["id"] for r in lst["results"]}, True)

    def test_zooming_in_reduces_and_moving_changes_hits(self):
        wide = self._get("api_points", bbox="5.9,45.8,10.5,47.8").json()["count"]
        zurich = self._get("api_points", bbox="8.40,47.30,8.70,47.45").json()
        bern = self._get("api_points", bbox="7.35,46.90,7.55,47.00").json()
        self.assertLess(zurich["count"], wide)
        self.assertGreater(zurich["count"], 0)
        self.assertTrue({p["id"] for p in zurich["points"]}.isdisjoint({p["id"] for p in bern["points"]}))

    def test_guest_responses_never_contain_member_or_internal_values(self):
        bodies = [
            self._get("api_points").content.decode(),
            *[self._get("api_list", page=p).content.decode() for p in range(1, 10)],
            self._get("home").content.decode(),
        ]
        for pub in PublishedLocation.objects.all()[:15]:
            bodies.append(self.client.get(reverse("locations:preview", args=[pub.public_id])).content.decode())
        for body in bodies:
            for marker in FORBIDDEN_IN_GUEST:
                self.assertNotIn(marker, body)

    def test_search_only_covers_guest_fields(self):
        for q in ("CANARY", "MITGLIEDERDETAIL", "example.org", "Demoweg", "Demo-Kontakt"):
            with self.subTest(q=q):
                self.assertEqual(self._get("api_points", q=q).json()["count"], 0)
        self.assertGreater(self._get("api_points", q="Zürich").json()["count"], 0)
        self.assertGreater(self._get("api_points", q="Beratung").json()["count"], 0)

    def test_invalid_parameters_are_rejected(self):
        for params in ({"bbox": "nan,1,2,3"}, {"bbox": "9,47,8,46"}, {"q": "x" * 81},
                       {"category": "admin"}, {"page": "0"}, {"page": "abc"}, {"page": "1001"}):
            with self.subTest(params=params):
                name = "api_list" if "page" in params else "api_points"
                r = self._get(name, **params)
                self.assertEqual(r.status_code, 400)
                self.assertNotIn("Traceback", r.content.decode())

    def test_drafts_and_unknown_ids_are_uniform_404(self):
        draft = Location.objects.filter(review_state="draft").first()
        r1 = self.client.get(reverse("locations:preview", args=[draft.public_id]))
        r2 = self.client.get(reverse("locations:preview", args=["doesnotexist"]))
        self.assertEqual((r1.status_code, r2.status_code), (404, 404))
        self.assertEqual(r1.content, r2.content)
        ids = {p["id"] for p in self._get("api_points").json()["points"]}
        self.assertNotIn(draft.public_id, ids)

    def test_preview_shows_guest_fields_and_login_hint(self):
        pub = PublishedLocation.objects.first()
        r = self.client.get(reverse("locations:preview", args=[pub.public_id]))
        self.assertContains(r, pub.title)
        self.assertContains(r, "Anmelden")
        self.assertEqual(r["Cache-Control"], "private, no-store")

    def test_head_requests_supported_on_public_pages(self):
        self.assertEqual(self.client.head("/").status_code, 200)
        pub = PublishedLocation.objects.first()
        self.assertEqual(self.client.head(reverse("locations:preview", args=[pub.public_id])).status_code, 200)

    def test_write_methods_not_allowed(self):
        self.assertEqual(self.client.post(reverse("locations:api_points")).status_code, 405)

    def test_too_many_points_returns_count_and_hint(self):
        from unittest import mock

        with mock.patch("locations.views.MAX_POINTS", 50):
            data = self._get("api_points").json()
        self.assertTrue(data["too_many"])
        self.assertEqual(data["count"], 90)
        self.assertEqual(data["points"], [])


class TileTests(TestCase):
    def setUp(self):
        self.client.get("/")

    def _tile_for(self, lat, lon, z):
        n = 2 ** z
        x = int((lon + 180) / 360 * n)
        y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
        return x, y

    def test_valid_swiss_tile_is_served_locally(self):
        x, y = self._tile_for(46.95, 7.44, 12)
        r = self.client.get(f"/map/tiles/12/{x}/{y}.jpeg")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "image/jpeg")
        self.assertTrue(r.content.startswith(b"\xff\xd8"))

    def test_rejects_out_of_range_and_foreign_tiles(self):
        x, y = self._tile_for(46.95, 7.44, 12)
        bad = [
            "/map/tiles/3/4/2.jpeg",             # Zoom zu klein
            "/map/tiles/25/1/1.jpeg",            # Zoom zu gross
            f"/map/tiles/12/{2**12}/{y}.jpeg",   # x ausserhalb
            "/map/tiles/12/0/0.jpeg",            # ausserhalb der Schweiz
            f"/map/tiles/12/{x}/{y}.png",        # falsches Format
            f"/map/tiles/12/-1/{y}.jpeg",
        ]
        for url in bad:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(f"/map/tiles/12/{x}/{y}.jpeg").status_code, 405)

    @override_settings(MAP_TILE_SOURCE="swisstopo")
    def test_tests_never_reach_real_provider(self):
        """Ohne Mock darf kein echter Abruf erfolgen (harte Netzwerksperre in Testsettings)."""
        from unittest import mock

        x, y = self._tile_for(46.95, 7.44, 12)
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Netzwerkzugriff!")) as net:
            r = self.client.get(f"/map/tiles/12/{x}/{y}.jpeg")
        self.assertEqual(r.status_code, 503)
        net.assert_not_called()

    @override_settings(MAP_TILE_SOURCE="unbekannt")
    def test_unknown_tile_source_is_404(self):
        x, y = self._tile_for(46.95, 7.44, 12)
        self.assertEqual(self.client.get(f"/map/tiles/12/{x}/{y}.jpeg").status_code, 404)


class AdminProtectionTests(TestCase):
    def test_admin_requires_otp_session(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user("staff@example.org", "Sehr-langes-Testpasswort-1")
        user.is_staff = True
        user.save()
        self.client.force_login(user)
        r = self.client.get("/verwaltung/")
        self.assertNotEqual(r.status_code, 200)
