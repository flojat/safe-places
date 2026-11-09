"""Etappe 3: Mitgliedsdetails, Kontingent, Fotos, Bildverarbeitung, Request-Limits (Auftrag 12 A/C/D)."""

import io
import threading
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from PIL import Image

from locations import rate_limits
from locations.models import DisclosureGrant, Location, Photo
from locations.services import images, publishing

from . import helpers_auth as h

MEMBER_MARKER = "MITGLIEDERDETAIL-"


def image_bytes(fmt="JPEG", size=(800, 600), color=(30, 120, 90), **save_kw) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt, **save_kw)
    return buf.getvalue()


def make_published(n: int, *, photos=True, **overrides):
    data = dict(
        title=f"Objekt {n}", category="beratung", preview_text=f"Vorschau {n}",
        long_description=f"{MEMBER_MARKER}{n} lang", place_text="Bern",
        exact_lat=Decimal("46.948000") + Decimal(n) / 1000, exact_lon=Decimal("7.447400"),
        exact_address=f"{MEMBER_MARKER}ADRESSE {n}", member_exact_location_released=True,
        contact_name=f"Kontakt {n}", contact_email=f"kontakt{n}@example.org", contacts_released=True,
        internal_notes="CANARY-INTERN",
    )
    data.update(overrides)
    loc = Location.objects.create(**data)
    created = {}
    if photos:
        for vis in (Photo.Visibility.GUEST_PREVIEW, Photo.Visibility.MEMBER_DETAIL, Photo.Visibility.INTERNAL):
            processed = images.process_upload(image_bytes())
            created[vis] = Photo.objects.create(location=loc, storage_key=processed.storage_key,
                                                alt_text=f"Alt {vis}", visibility=vis)
    publishing.submit(loc.pk, None, loc.work_version)
    pub = publishing.publish(loc.pk, None, loc.work_version)
    return loc, pub, created


def member_client(email="mitglied@example.org"):
    client = Client()
    pw = h.random_password()
    user = h.make_active_user(email, pw)
    h.login_password(client, email, pw)
    h.setup_totp(client)
    return client, user


def photo_url(photo, variant="vorschau"):
    return reverse("locations:photo", args=[photo.public_id, variant])


class MemberDetailTests(TestCase):
    def setUp(self):
        self.loc, self.pub, self.photos = make_published(1)
        self.member, self.user = member_client()

    def _open(self, client=None, pub=None):
        return (client or self.member).post(reverse("locations:open_details", args=[(pub or self.pub).public_id]))

    def _view(self, client=None, pub=None):
        return (client or self.member).get(reverse("locations:preview", args=[(pub or self.pub).public_id]))

    def test_guest_sees_preview_only_and_cannot_open(self):
        guest = Client()
        r = guest.get(reverse("locations:preview", args=[self.pub.public_id]))
        self.assertNotContains(r, MEMBER_MARKER)
        self.assertNotContains(r, "kontakt1@example.org")
        r = guest.post(reverse("locations:open_details", args=[self.pub.public_id]))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("two_factor:login"), r["Location"])
        self.assertFalse(DisclosureGrant.objects.exists())

    def test_password_only_session_gets_no_details(self):
        client = Client()
        pw = h.random_password()
        user = h.make_active_user("halb@example.org", pw)
        h.login_password(client, user.email, pw)  # kein Faktor -> Einrichtungszustand
        r = client.post(reverse("locations:open_details", args=[self.pub.public_id]))
        self.assertRedirects(r, reverse("two_factor:setup"), fetch_redirect_response=False)
        self.assertNotContains(client.get(reverse("locations:preview", args=[self.pub.public_id])), MEMBER_MARKER)
        self.assertFalse(DisclosureGrant.objects.exists())

    def test_get_never_books_and_post_reveals_details_and_contacts(self):
        r = self._view()
        self.assertContains(r, "Details und Kontakte anzeigen")
        self.assertNotContains(r, MEMBER_MARKER)
        self.assertFalse(DisclosureGrant.objects.exists())
        r = self._open()
        self.assertEqual(r.status_code, 302)
        r = self._view()
        self.assertContains(r, f"{MEMBER_MARKER}1 lang")
        self.assertContains(r, "kontakt1@example.org")
        self.assertContains(r, f"{MEMBER_MARKER}ADRESSE 1")
        self.assertNotContains(r, "CANARY")
        self.assertEqual(r["Cache-Control"], "private, no-store")

    def test_reopen_same_version_does_not_count_again(self):
        self._open()
        events = rate_limits.RateEvent.objects.filter(bucket__scope="grant_account").count()
        self._open()
        self._view()
        self.assertEqual(rate_limits.RateEvent.objects.filter(bucket__scope="grant_account").count(), events)
        self.assertEqual(DisclosureGrant.objects.count(), 1)

    @override_settings(RATE_LIMITS={**settings.RATE_LIMITS, "grant_account": [(5, 900), (3, 86400)]})
    def test_quota_rejects_before_any_sensitive_output(self):
        pubs = [self.pub] + [make_published(n, photos=False)[1] for n in (2, 3, 4)]
        for pub in pubs[:3]:
            self.assertEqual(self._open(pub=pub).status_code, 302)
        r = self._open(pub=pubs[3])
        self.assertEqual(r.status_code, 429)
        self.assertIn("Retry-After", r)
        self.assertNotContains(r, MEMBER_MARKER, status_code=429)
        self.assertNotContains(r, "kontakt4@example.org", status_code=429)
        self.assertNotContains(self._view(pub=pubs[3]), MEMBER_MARKER)
        # Bereits geoeffnete Objekte bleiben lesbar.
        self.assertContains(self._view(pub=pubs[0]), f"{MEMBER_MARKER}1")

    @override_settings(RATE_LIMITS={**settings.RATE_LIMITS, "grant_account": [(5, 900), (1, 86400)]})
    def test_quota_is_shared_by_all_sessions_of_an_account(self):
        from django_otp.plugins.otp_totp.models import TOTPDevice

        pw = h.random_password()
        user = h.make_active_user("zwei@example.org", pw)
        first, second = Client(), Client()
        h.login_password(first, user.email, pw)
        _, key = h.setup_totp(first)
        h.login_password(second, user.email, pw)
        h.login_token(second, h.totp_code(key, 1))
        self.assertTrue(h.is_fully_verified(first) and h.is_fully_verified(second))
        other_pub = make_published(2, photos=False)[1]
        self.assertEqual(self._open(client=first).status_code, 302)
        r = self._open(client=second, pub=other_pub)
        self.assertEqual(r.status_code, 429)  # zweite Sitzung: gleiches Kontokontingent
        self.assertTrue(TOTPDevice.objects.filter(user=user).exists())

    def test_new_disclosure_version_requires_new_booking(self):
        self._open()
        Location.objects.filter(pk=self.loc.pk).update(contact_phone="+41 00 000 1234", work_version=2)
        self.loc.refresh_from_db()
        publishing.submit(self.loc.pk, None, 2)
        publishing.publish(self.loc.pk, None, 2)
        r = self._view()
        self.assertNotContains(r, MEMBER_MARKER)
        self.assertContains(r, "Details und Kontakte anzeigen")

    def test_withdrawal_stops_delivery_despite_booking(self):
        self._open()
        member_photo = self.photos[Photo.Visibility.MEMBER_DETAIL]
        self.assertEqual(self.member.get(photo_url(member_photo, "detail")).status_code, 200)
        publishing.withdraw(self.loc.pk, None)
        self.assertEqual(self._view().status_code, 404)
        self.assertEqual(self.member.get(photo_url(member_photo, "detail")).status_code, 404)

    def test_logout_ends_member_access(self):
        self._open()
        self.member.post(reverse("account:logout"))
        r = self._view()
        self.assertNotContains(r, MEMBER_MARKER)
        member_photo = self.photos[Photo.Visibility.MEMBER_DETAIL]
        self.assertEqual(self.member.get(photo_url(member_photo)).status_code, 404)

    def test_limiter_failure_returns_503_without_booking(self):
        with mock.patch("locations.member_access.rate_limits.reserve",
                        side_effect=rate_limits.RateLimiterUnavailable()):
            r = self._open()
        self.assertEqual(r.status_code, 503)
        self.assertFalse(DisclosureGrant.objects.exists())

    def test_member_list_page_size(self):
        for n in range(2, 14):
            make_published(n, photos=False)
        self.member.get("/")
        data = self.member.get(reverse("locations:api_list")).json()
        self.assertEqual(data["page_size"], 50)
        self.assertEqual(len(data["results"]), 13)
        guest = Client()
        guest.get("/")
        self.assertEqual(guest.get(reverse("locations:api_list")).json()["page_size"], 10)

    def test_member_list_still_guest_projection(self):
        self._open()
        body = self.member.get(reverse("locations:api_list")).content.decode()
        self.assertNotIn(MEMBER_MARKER, body)
        self.assertNotIn("kontakt1@example.org", body)


class PhotoAccessTests(TestCase):
    def setUp(self):
        self.loc, self.pub, self.photos = make_published(1)
        self.guest = Client()
        self.guest.get("/")
        self.g = self.photos[Photo.Visibility.GUEST_PREVIEW]
        self.m = self.photos[Photo.Visibility.MEMBER_DETAIL]
        self.i = self.photos[Photo.Visibility.INTERNAL]

    def test_guest_gets_only_guest_thumbnail(self):
        r = self.guest.get(photo_url(self.g))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "image/jpeg")
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r["Cache-Control"], "private, no-store")
        for url in (photo_url(self.g, "detail"), photo_url(self.m), photo_url(self.m, "detail"),
                    photo_url(self.i), photo_url(self.g, "original"),
                    reverse("locations:photo", args=["unbekannt", "vorschau"])):
            with self.subTest(url=url):
                self.assertEqual(self.guest.get(url).status_code, 404)

    def test_member_needs_booking_and_never_gets_internal(self):
        member, _ = member_client()
        self.assertEqual(member.get(photo_url(self.m)).status_code, 404)
        member.post(reverse("locations:open_details", args=[self.pub.public_id]))
        self.assertEqual(member.get(photo_url(self.m)).status_code, 200)
        self.assertEqual(member.get(photo_url(self.m, "detail")).status_code, 200)
        self.assertEqual(member.get(photo_url(self.g, "detail")).status_code, 200)
        self.assertEqual(member.get(photo_url(self.i)).status_code, 404)

    def test_withdrawn_photo_not_delivered(self):
        Photo.objects.filter(pk__in=[self.g.pk, self.m.pk]).update(is_withdrawn=True)
        self.assertEqual(self.guest.get(photo_url(self.g)).status_code, 404)
        self.assertNotContains(self.guest.get(reverse("locations:preview", args=[self.pub.public_id])),
                               photo_url(self.g))

    def test_draft_photos_only_for_verified_admin(self):
        draft = Location.objects.create(title="Entwurf")
        processed = images.process_upload(image_bytes())
        photo = Photo.objects.create(location=draft, storage_key=processed.storage_key,
                                     visibility=Photo.Visibility.MEMBER_DETAIL)
        self.assertEqual(self.guest.get(photo_url(photo)).status_code, 404)
        member, _ = member_client()
        self.assertEqual(member.get(photo_url(photo)).status_code, 404)
        admin = Client()
        pw = h.random_password()
        user = h.make_active_user("admin@example.org", pw)
        user.is_staff = user.is_superuser = True
        user.save()
        h.login_password(admin, user.email, pw)
        self.assertEqual(admin.get(photo_url(photo)).status_code, 404)  # ohne zweiten Schritt
        h.setup_totp(admin)
        self.assertEqual(admin.get(photo_url(photo)).status_code, 200)

    @override_settings(DEBUG=True)
    def test_no_public_media_route_even_in_debug(self):
        for path in (f"/media/{self.m.storage_key[:2]}/{self.m.storage_key}/detail.jpg",
                     f"/var/private_media/{self.m.storage_key[:2]}/{self.m.storage_key}/detail.jpg"):
            self.assertEqual(self.guest.get(path).status_code, 404)

    def test_guest_list_contains_only_guest_thumb(self):
        body = self.guest.get(reverse("locations:api_list")).content.decode()
        self.assertIn(self.g.public_id, body)
        self.assertNotIn(self.m.public_id, body)
        self.assertNotIn(self.i.public_id, body)


class ImageProcessingTests(TestCase):
    def test_valid_formats_produce_bounded_variants(self):
        for fmt in ("JPEG", "PNG", "WEBP"):
            with self.subTest(fmt=fmt):
                result = images.process_upload(image_bytes(fmt, size=(3000, 2000)))
                thumb = Image.open(images.variant_path(result.storage_key, "thumb"))
                detail = Image.open(images.variant_path(result.storage_key, "detail"))
                self.assertEqual(thumb.format, "JPEG")
                self.assertLessEqual(max(thumb.size), 400)
                self.assertLessEqual(max(detail.size), 1600)

    def test_exif_and_gps_removed_and_orientation_applied(self):
        img = Image.new("RGB", (200, 100), (200, 30, 30))
        exif = Image.Exif()
        exif[0x0112] = 6  # Orientation: 90 Grad drehen
        exif[0x8825] = {2: (46.0, 57.0, 0.0), 1: "N"}  # GPSInfo
        exif[0x010F] = "Kamerahersteller"
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif)
        result = images.process_upload(buf.getvalue())
        out = Image.open(images.variant_path(result.storage_key, "detail"))
        self.assertEqual(out.size, (100, 200))
        self.assertEqual(len(out.getexif()), 0)
        raw = images.variant_path(result.storage_key, "detail").read_bytes()
        self.assertNotIn(b"Kamerahersteller", raw)

    def test_rejections(self):
        animated = io.BytesIO()
        frames = [Image.new("RGB", (50, 50), c) for c in ((255, 0, 0), (0, 255, 0))]
        frames[0].save(animated, format="WEBP", save_all=True, append_images=frames[1:])
        gif = io.BytesIO()
        Image.new("RGB", (50, 50)).save(gif, format="GIF")
        cases = {
            "text": b"hallo, ich bin kein Bild",
            "svg": b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            "html": b"<html><body>x</body></html>",
            "broken_jpeg": image_bytes()[:200],
            "gif": gif.getvalue(),
            "animated_webp": animated.getvalue(),
            "empty": b"",
        }
        for name, data in cases.items():
            with self.subTest(name=name), self.assertRaises(images.ImageRejected):
                images.process_upload(data)

    @override_settings(IMAGE_MAX_UPLOAD_BYTES=1000)
    def test_oversize_bytes_rejected(self):
        with self.assertRaises(images.ImageRejected):
            images.process_upload(image_bytes(size=(800, 600)))

    def test_decompression_bomb_rejected(self):
        bomb = io.BytesIO()
        Image.new("1", (12000, 12000)).save(bomb, format="PNG")  # klein komprimiert, 144 MP
        self.assertLess(len(bomb.getvalue()), settings.IMAGE_MAX_UPLOAD_BYTES)
        with self.assertRaises(images.ImageRejected):
            images.process_upload(bomb.getvalue())

    def test_media_dirs_private_despite_umask(self):
        import os

        old = os.umask(0o022)
        try:
            result = images.process_upload(image_bytes())
        finally:
            os.umask(old)
        d = images.storage_dir(result.storage_key)
        for p in (d, d.parent):
            self.assertEqual(p.stat().st_mode & 0o077, 0, str(p))

    def test_storage_paths_cannot_escape(self):
        for key in ("../../etc", "a" * 31, "G" * 32, "", "../" + "a" * 29):
            with self.subTest(key=key), self.assertRaises(ValueError):
                images.storage_dir(key)
        with self.assertRaises(ValueError):
            images.variant_path("a" * 32, "../secret")


class RequestLimitTests(TestCase):
    def setUp(self):
        make_published(1, photos=False)

    def test_api_without_session_is_refused(self):
        r = Client().get(reverse("locations:api_points"))
        self.assertEqual(r.status_code, 403)
        x, y = 2132, 1441
        self.assertEqual(Client().get(f"/map/tiles/12/{x}/{y}.jpeg").status_code, 403)

    def test_guest_query_limit_and_spoofed_forwarded_headers(self):
        client = Client()
        client.get("/")  # zaehlt nicht als Abfrage
        statuses = [
            client.get(reverse("locations:api_points"), HTTP_X_FORWARDED_FOR=f"198.51.100.{i}",
                       HTTP_X_REAL_IP=f"203.0.113.{i}").status_code
            for i in range(13)
        ]
        self.assertEqual(statuses[:12], [200] * 12)
        self.assertEqual(statuses[12], 429)
        r = client.get(reverse("locations:api_list"))
        self.assertEqual(r.status_code, 429)
        self.assertIn("Retry-After", r)
        # Login/Registrierung bleiben bedienbar.
        self.assertEqual(client.get(reverse("two_factor:login")).status_code, 200)
        self.assertEqual(client.get(reverse("account:register")).status_code, 200)

    def test_unknown_preview_ids_count_against_limit(self):
        client = Client()
        client.get("/")
        for i in range(12):
            client.get(reverse("locations:preview", args=[f"gibtsnicht{i}"]))
        self.assertEqual(client.get(reverse("locations:preview", args=["gibtsnicht99"])).status_code, 429)

    def test_member_has_higher_query_limit(self):
        member, _ = member_client()
        statuses = {member.get(reverse("locations:api_points")).status_code for _ in range(15)}
        self.assertEqual(statuses, {200})

    def test_new_guest_session_creation_is_ip_limited(self):
        with override_settings(RATE_LIMITS={**settings.RATE_LIMITS, "guest_session_ip": [(2, 3600)]}):
            for _ in range(2):
                c = Client()
                c.get("/")
                self.assertEqual(c.get(reverse("locations:api_points")).status_code, 200)
            c = Client()
            self.assertEqual(c.get("/").status_code, 200)  # Seite bleibt erreichbar
            self.assertEqual(c.get(reverse("locations:api_points")).status_code, 403)


class GrantConcurrencyTests(TransactionTestCase):
    def test_parallel_opens_never_exceed_quota_and_never_double_book(self):
        pubs = [make_published(n, photos=False)[1] for n in range(1, 9)]
        member, user = member_client("parallel@example.org")
        cookies = member.cookies
        results, barrier = [], threading.Barrier(len(pubs) + 3)

        def worker(pub):
            c = Client()
            c.cookies = cookies
            try:
                barrier.wait()
                results.append(c.post(reverse("locations:open_details", args=[pub.public_id])).status_code)
            finally:
                connection.close()

        # 8 verschiedene Objekte (Limit 5/15 min) plus 3 parallele Wiederholungen desselben Objekts.
        targets = pubs + [pubs[0], pubs[0], pubs[0]]
        threads = [threading.Thread(target=worker, args=(p,)) for p in targets]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        grants = DisclosureGrant.objects.filter(user=user)
        self.assertEqual(grants.count(), 5)
        self.assertEqual(rate_limits.RateEvent.objects.filter(bucket__scope="grant_account").count(), 5)
        self.assertEqual(set(results) - {302, 429}, set())
        # Objekt 0 hoechstens einmal gebucht; wurde es gebucht, erhalten alle 4 Aufrufe 302.
        booked_first = grants.filter(published=pubs[0]).count()
        self.assertLessEqual(booked_first, 1)
        self.assertEqual(results.count(302), 5 + (3 if booked_first else 0))
