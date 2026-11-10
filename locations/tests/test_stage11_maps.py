"""Punktwahl auf der Karte (Portal/Verwaltung) und Grundkarte Luftbild."""

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from locations.roles import EDITOR, OBJECT_MANAGER

from .test_stage1_guest import make_location
from .test_stage4_work import verified


class PickerPresenceTests(TestCase):
    def test_portal_and_admin_offer_map_picker_next_to_address_search(self):
        owner_client, owner = verified("o@example.org", groups=[OBJECT_MANAGER])
        loc = make_location(managed_by=owner)
        portal = owner_client.get(reverse("portal:edit", args=[loc.public_id])).content.decode()
        editor_client, _ = verified("r@example.org", groups=[EDITOR], staff=True)
        admin = editor_client.get(reverse("admin:locations_location_change", args=[loc.pk])).content.decode()
        for page in (portal, admin):
            self.assertIn('data-location-picker data-lat-field="id_exact_lat" data-lon-field="id_exact_lon"', page)
            self.assertIn("Punkt auf Karte wählen", page)
            self.assertIn("js/location_picker.js", page)
            self.assertIn("js/basemaps.js", page)
            self.assertIn("vendor/leaflet/leaflet.js", page)
            self.assertIn('id="id_exact_lat"', page)

    def test_server_still_validates_coordinates_from_picker(self):
        from .test_stage4_work import edit_post

        owner_client, owner = verified("o@example.org", groups=[OBJECT_MANAGER])
        loc = make_location(managed_by=owner)
        r = owner_client.post(reverse("portal:edit", args=[loc.public_id]),
                              edit_post(loc, exact_lat="48.9", exact_lon="2.35"))  # Paris
        self.assertEqual(r.status_code, 200)
        loc.refresh_from_db()
        self.assertNotEqual(str(loc.exact_lat), "48.900000")


@override_settings(MAP_TILE_SOURCE="local_test")
class AerialTileTests(TestCase):
    def test_local_test_aerial_tiles_are_served_on_own_route(self):
        guest = Client()
        guest.get("/")
        x, y = 2140, 1434  # Zoom 12, Bern
        karte = guest.get(f"/map/tiles/12/{x}/{y}.jpeg")
        luftbild = guest.get(reverse("locations:tile_aerial", args=[12, x, y]))
        self.assertEqual((karte.status_code, luftbild.status_code), (200, 200))
        self.assertEqual(luftbild["Content-Type"], "image/jpeg")
        self.assertNotEqual(karte.content, luftbild.content)
        self.assertEqual(guest.get(f"/map/luftbild/12/0/0.jpeg").status_code, 404)  # ausserhalb CH

    def test_home_loads_basemap_switch(self):
        page = Client().get("/").content.decode()
        self.assertIn("js/basemaps.js", page)
        self.assertIn("css/map_tools.css", page)
