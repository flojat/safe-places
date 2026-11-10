"""Bildergalerie nach Berechtigung, Bildbeschreibung (zugleich Alt-Text) und Bestaetigung der Bildrechte."""

import json
import re

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from locations.models import AuditEvent, Photo
from locations.roles import EDITOR, OBJECT_MANAGER
from locations.services import publishing

from .test_stage1_guest import make_location
from .test_stage3_member import image_bytes, make_published, member_client
from .test_stage4_work import verified


def gallery_data(html: str) -> list[dict]:
    match = re.search(r'<script id="gallery-data" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(match.group(1)) if match else []


class GalleryPermissionTests(TestCase):
    def setUp(self):
        self.loc, self.pub, self.photos = make_published(1)
        self.g = self.photos[Photo.Visibility.GUEST_PREVIEW]
        self.m = self.photos[Photo.Visibility.MEMBER_DETAIL]
        self.i = self.photos[Photo.Visibility.INTERNAL]
        self.url = reverse("locations:preview", args=[self.pub.public_id])

    def ids(self, items):
        return {item["full"].split("/")[2] for item in items}

    def test_guest_gallery_contains_only_guest_photos_with_description(self):
        html = Client().get(self.url).content.decode()
        items = gallery_data(html)
        self.assertEqual(self.ids(items), {self.g.public_id})
        self.assertEqual(items[0]["description"], self.g.alt_text)
        self.assertIn(f'alt="{self.g.alt_text}"', html)
        for photo in (self.m, self.i):
            self.assertNotIn(photo.public_id, html)

    def test_member_without_booking_sees_guest_gallery_only(self):
        member, _ = member_client()
        html = member.get(self.url).content.decode()
        self.assertEqual(self.ids(gallery_data(html)), {self.g.public_id})
        self.assertNotIn(self.m.public_id, html)

    def test_member_with_booking_sees_guest_and_member_photos_never_internal(self):
        member, _ = member_client()
        member.post(reverse("locations:open_details", args=[self.pub.public_id]))
        html = member.get(self.url).content.decode()
        self.assertEqual(self.ids(gallery_data(html)), {self.g.public_id, self.m.public_id})
        self.assertNotIn(self.i.public_id, html)
        for item in gallery_data(html):
            self.assertEqual(member.get(item["full"]).status_code, 200)

    def test_every_guest_gallery_url_is_deliverable_to_guests(self):
        guest = Client()
        for item in gallery_data(guest.get(self.url).content.decode()):
            self.assertEqual(guest.get(item["thumb"]).status_code, 200)
            self.assertEqual(guest.get(item["full"]).status_code, 200)
        self.assertEqual(guest.get(reverse("locations:photo", args=[self.m.public_id, "detail"])).status_code, 404)

    def test_list_api_gallery_is_guest_only_and_skips_withdrawn(self):
        guest = Client()
        guest.get("/")
        item = guest.get(reverse("locations:api_list")).json()["results"][0]
        self.assertEqual(self.ids(item["gallery"]), {self.g.public_id})
        publishing.withdraw_photo(self.g.pk, None)
        item = guest.get(reverse("locations:api_list")).json()["results"][0]
        self.assertEqual(item["gallery"], [])
        self.assertNotIn(self.g.public_id, Client().get(self.url).content.decode())


class PhotoRightsTests(TestCase):
    def setUp(self):
        self.owner_client, self.owner = verified("o@example.org", groups=[OBJECT_MANAGER])
        self.loc = make_location(managed_by=self.owner)
        self.upload_url = reverse("portal:photo_upload", args=[self.loc.public_id])

    def upload(self, client, url, **extra):
        data = {"image": SimpleUploadedFile("x.jpg", image_bytes(), content_type="image/jpeg"),
                "visibility": "guest_preview", "alt_text": "Eingang mit Rampe"}
        data.update(extra)
        return client.post(url, data, follow=True)

    def test_portal_shows_copyright_notice_and_description_label(self):
        page = self.owner_client.get(reverse("portal:edit", args=[self.loc.public_id])).content.decode()
        self.assertIn("Bildrechte (Copyright)", page)
        self.assertIn("eigene Bilder", page)
        self.assertIn("Bildbeschreibung", page)
        self.assertNotIn("Alternativtext<", page)
        self.assertIn('name="rights_confirmed"', page)

    def test_upload_without_rights_confirmation_is_refused(self):
        r = self.upload(self.owner_client, self.upload_url)
        self.assertContains(r, "Bildrechte")
        self.assertFalse(Photo.objects.filter(location=self.loc).exists())

    def test_upload_with_confirmation_records_it(self):
        self.upload(self.owner_client, self.upload_url, rights_confirmed="on")
        photo = Photo.objects.get(location=self.loc)
        self.assertIsNotNone(photo.rights_confirmed_at)
        self.assertEqual(photo.alt_text, "Eingang mit Rampe")
        self.assertTrue(AuditEvent.objects.filter(action="photo_upload", target_id=photo.public_id,
                                                  detail="bildrechte_bestaetigt").exists())

    def test_editor_upload_also_needs_confirmation(self):
        editor, _ = verified("r@example.org", groups=[EDITOR], staff=True)
        url = reverse("admin:locations_location_photos", args=[self.loc.pk])
        page = editor.get(url).content.decode()
        self.assertIn("Bildrechte (Copyright)", page)
        self.upload(editor, url, action="upload")
        self.assertFalse(Photo.objects.filter(location=self.loc).exists())
        self.upload(editor, url, action="upload", rights_confirmed="on")
        self.assertIsNotNone(Photo.objects.get(location=self.loc).rights_confirmed_at)
