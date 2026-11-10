"""Browser-Integrationstest (Playwright/Chromium) fuer die Gastansicht.

Fehlt der Browser, wird der Test als BLOCKIERT uebersprungen und nicht als bestanden gewertet.
"""

import os
import unittest
from io import StringIO
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(settings.BASE_DIR / ".playwright-browsers"))

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, "BLOCKIERT: Playwright nicht installiert")
class GuestBrowserTests(StaticLiveServerTestCase):
    host = "127.0.0.1"

    @classmethod
    def setUpClass(cls):
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch()
        except Exception as exc:  # pragma: no cover
            cls.pw.stop()
            super().tearDownClass()
            raise unittest.SkipTest(f"BLOCKIERT: Chromium nicht startbar ({type(exc).__name__})")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        super().tearDownClass()

    def setUp(self):
        call_command("seed_demo", "--count", "100", "--no-photos", stdout=StringIO())

    def _page(self, **kw):
        ctx = self.browser.new_context(**kw)
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        requests = []
        page.on("request", lambda req: requests.append(req.url))
        return page, requests

    def test_desktop_map_and_list_are_synchronized(self):
        page, requests = self._page(viewport={"width": 1280, "height": 800})
        page.goto(self.live_server_url + "/")
        page.wait_for_selector("#results > li")
        page.wait_for_selector(".leaflet-interactive")
        self.assertEqual(page.locator("#results > li").count(), 10)
        start_status = page.text_content("#status")
        start_markers = page.locator("path.leaflet-interactive").count()
        self.assertGreater(start_markers, 0)

        # Hineinzoomen auf Zuerich reduziert die Treffer in Liste und Karte.
        with page.expect_response(lambda r: "/api/punkte" in r.url):
            page.evaluate("document.querySelector('#map') && window.dispatchEvent(new Event('noop'))")
            page.locator("#map").dblclick(position={"x": 640, "y": 300})
        page.wait_for_timeout(800)
        zoomed_status = page.text_content("#status")
        self.assertNotEqual(start_status, zoomed_status)

        # Klick in der Liste markiert den Eintrag.
        page.locator("#results > li").first.click(position={"x": 5, "y": 40})
        self.assertEqual(page.locator("#results > li.active").count(), 1)

        # Keine Requests an fremde Hosts (insbesondere kein Direktzugriff auf swisstopo).
        live_host = urlparse(self.live_server_url).netloc
        foreign = [u for u in requests if urlparse(u).netloc not in (live_host, "")]
        self.assertEqual(foreign, [])
        self.assertTrue(any("/map/tiles/" in u for u in requests))

        # Entprellung: eine Bewegung erzeugt hoechstens je eine Punkt- und Listenanfrage.
        before = len([u for u in requests if "/api/" in u])
        page.mouse.move(800, 400)
        page.mouse.down(); page.mouse.move(700, 420, steps=10); page.mouse.up()
        page.wait_for_timeout(1000)
        after = [u for u in requests if "/api/" in u][before:]
        self.assertLessEqual(len([u for u in after if "/api/punkte" in u]), 1)
        self.assertLessEqual(len([u for u in after if "/api/liste" in u]), 1)

    def test_guest_page_contains_no_member_data(self):
        page, _ = self._page(viewport={"width": 1280, "height": 800})
        page.goto(self.live_server_url + "/")
        page.wait_for_selector("#results > li")
        html = page.content()
        for marker in ("CANARY-", "MITGLIEDERDETAIL-", "@example.org"):
            self.assertNotIn(marker, html)
        page.locator("#results > li h2 a").first.click()
        page.wait_for_selector("article.preview")
        self.assertIn("nur für angemeldete Mitglieder", page.content())
        self.assertNotIn("MITGLIEDERDETAIL-", page.content())

    def test_category_checkboxes_combine_and_cards_show_tags(self):
        page, requests = self._page(viewport={"width": 1280, "height": 800})
        page.goto(self.live_server_url + "/")
        page.wait_for_selector("#results > li .tags .tag")
        all_status = page.text_content("#status")
        with page.expect_response(lambda r: "/api/liste" in r.url and "category=bildung" in r.url):
            page.check('#category-filter input[value="bildung"]')
        page.wait_for_timeout(300)
        only_one = page.text_content("#status")
        with page.expect_response(lambda r: "/api/liste" in r.url and "category=bildung" in r.url
                                  and "category=freizeit" in r.url):
            page.check('#category-filter input[value="freizeit"]')
        page.wait_for_timeout(300)
        both = page.text_content("#status")
        count = lambda text: int(text.split()[0])  # noqa: E731
        self.assertLess(count(only_one), count(all_status))
        self.assertGreater(count(both), count(only_one))
        self.assertIn("category=bildung", page.url)
        self.assertIn("category=freizeit", page.url)
        # Jede Karte zeigt mindestens eine der gewaehlten Kategorien.
        for card_tags in page.locator("#results > li .tags").all_text_contents():
            self.assertTrue("Bildung" in card_tags or "Freizeit" in card_tags, card_tags)
        # Karte nimmt sichtbar mehr als die fruehere feste Breite ein.
        width = page.evaluate("document.getElementById('list-panel').getBoundingClientRect().width")
        self.assertGreater(width, 26 * 16)

    def test_gallery_opens_with_description_and_only_guest_photos(self):
        from locations.models import Photo
        from locations.services import images
        from locations.services import publishing as pub_service

        from .test_stage3_member import image_bytes, make_published

        loc, pub, _created = make_published(1)
        extra = images.process_upload(image_bytes())
        Photo.objects.create(location=loc, storage_key=extra.storage_key, alt_text="Zweites Gastbild Garten",
                             visibility=Photo.Visibility.GUEST_PREVIEW, sort_order=5)
        loc.refresh_from_db()
        pub_service.submit(loc.pk, None, loc.work_version)
        pub_service.publish(loc.pk, None, loc.work_version)

        page, _ = self._page(viewport={"width": 1280, "height": 800})
        page.goto(self.live_server_url + f"/objekt/{pub.public_id}/")
        self.assertEqual(page.locator(".gallery-item").count(), 2)  # nur Gastbilder
        page.locator(".gallery-item").first.click()
        page.wait_for_selector(".lightbox:not([hidden])")
        page.wait_for_function("document.querySelector('.lightbox img').naturalWidth > 0")
        self.assertIn("Alt guest_preview", page.text_content(".lightbox-desc"))
        self.assertEqual(page.get_attribute(".lightbox img", "alt"), page.text_content(".lightbox-desc"))
        self.assertEqual(page.text_content(".lightbox-count"), "1 / 2")
        page.keyboard.press("ArrowRight")
        self.assertEqual(page.text_content(".lightbox-desc"), "Zweites Gastbild Garten")
        self.assertEqual(page.text_content(".lightbox-count"), "2 / 2")
        page.keyboard.press("Escape")
        self.assertTrue(page.locator(".lightbox").is_hidden())

    def test_visitor_switches_to_aerial_imagery(self):
        page, requests = self._page(viewport={"width": 1280, "height": 800})
        page.goto(self.live_server_url + "/")
        page.wait_for_selector(".basemap-toggle")
        self.assertEqual(page.get_attribute('.basemap-toggle [data-basemap="karte"]', "aria-pressed"), "true")
        self.assertFalse(any("/map/luftbild/" in u for u in requests))
        with page.expect_response(lambda r: "/map/luftbild/" in r.url and r.status == 200):
            page.click('.basemap-toggle [data-basemap="luftbild"]')
        self.assertEqual(page.get_attribute('.basemap-toggle [data-basemap="luftbild"]', "aria-pressed"), "true")
        self.assertIn("basis=luftbild", page.url)
        # Wahl bleibt beim erneuten Laden erhalten (URL), Rueckweg zur Karte moeglich.
        page.reload()
        page.wait_for_selector(".basemap-toggle")
        self.assertEqual(page.get_attribute('.basemap-toggle [data-basemap="luftbild"]', "aria-pressed"), "true")
        with page.expect_response(lambda r: "/map/tiles/" in r.url):
            page.click('.basemap-toggle [data-basemap="karte"]')
        self.assertNotIn("basis=luftbild", page.url)

    def test_owner_picks_point_on_map_and_takes_coordinates(self):
        from locations.roles import OBJECT_MANAGER

        from .test_stage1_guest import make_location
        from .test_stage4_work import verified

        client, user = verified("betreuung-karte@example.org", groups=[OBJECT_MANAGER])
        loc = make_location(title="Waldhaus", managed_by=user, exact_lat=None, exact_lon=None)
        page, _ = self._page(viewport={"width": 1280, "height": 900})
        page.context.add_cookies([{"name": settings.SESSION_COOKIE_NAME,
                                   "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                                   "url": self.live_server_url}])
        page.goto(self.live_server_url + f"/meine-objekte/{loc.public_id}/")
        self.assertEqual(page.input_value("#id_exact_lat"), "")
        page.click("[data-location-picker] button")
        page.wait_for_selector(".picker-dialog[open] .leaflet-tile-loaded")
        self.assertTrue(page.is_disabled(".picker-foot button:not(.secondary)"))
        page.locator(".picker-map").click(position={"x": 400, "y": 250})
        self.assertIn("Gewählt:", page.text_content(".picker-coords"))
        page.click('.picker-dialog .basemap-toggle [data-basemap="luftbild"]')
        page.click(".picker-foot button:not(.secondary)")
        lat, lon = float(page.input_value("#id_exact_lat")), float(page.input_value("#id_exact_lon"))
        self.assertTrue(45.8 <= lat <= 47.85 and 5.9 <= lon <= 10.55, (lat, lon))
        self.assertIn("Koordinaten übernommen", page.text_content(".picker-status"))
        self.assertFalse(page.locator(".picker-dialog").evaluate("d => d.open"))

    def test_mobile_toggle_loads_map_only_when_shown(self):
        page, requests = self._page(viewport={"width": 390, "height": 800}, is_mobile=True)
        page.goto(self.live_server_url + "/")
        page.wait_for_selector("#results > li")
        self.assertFalse(any("/map/tiles/" in u for u in requests))
        page.click("#show-map")
        page.wait_for_selector(".leaflet-interactive")
        self.assertTrue(page.is_visible("#map"))
        self.assertTrue(any("/map/tiles/" in u for u in requests))
        page.click("#show-list")
        self.assertTrue(page.is_visible("#results"))


@unittest.skipIf(sync_playwright is None, "BLOCKIERT: Playwright nicht installiert")
class AuthBrowserTests(StaticLiveServerTestCase):
    """Echter Browserablauf: Registrierung, Bestaetigung, Login, E-Mail-Code-Einrichtung."""

    host = "127.0.0.1"

    @classmethod
    def setUpClass(cls):
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        super().tearDownClass()

    def test_register_verify_login_and_email_code_setup(self):
        import re

        from django.core import mail

        from . import helpers_auth as h

        shots = os.environ.get("ADRESSKARTE_SCREENSHOT_DIR")
        ctx = self.browser.new_context(viewport={"width": 1100, "height": 800})
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        password = h.random_password()
        base = self.live_server_url

        page.goto(base + "/konto/registrieren/")
        page.fill("#id_email", "browser@example.org")
        page.fill("#id_password1", password)
        page.fill("#id_password2", password)
        page.click("button[type=submit]")
        page.wait_for_selector("text=Bitte E-Mail prüfen")

        link = re.search(r"https?://\S+", mail.outbox[-1].body).group(0)
        page.goto(link)
        self.assertNotIn("email-bestaetigen/" + link.rsplit("/", 2)[-2], page.url)  # tokenfreie URL
        page.click("button:has-text('E-Mail-Adresse bestätigen')")
        page.wait_for_selector("text=E-Mail-Adresse bestätigt")

        page.goto(base + "/konto/anmelden/")
        if shots:
            page.screenshot(path=f"{shots}/login.png")
        page.fill("input[name='auth-username']", "browser@example.org")
        page.fill("input[name='auth-password']", password)
        page.get_by_role("button", name="Weiter", exact=True).click()
        page.wait_for_selector("text=Zweiten Anmeldeschritt einrichten")
        page.get_by_role("button", name="Weiter", exact=True).click()  # Willkommen
        if shots:
            page.screenshot(path=f"{shots}/setup_method.png")
        page.check("input[value='email']")
        page.get_by_role("button", name="Weiter", exact=True).click()
        page.wait_for_selector("text=Wir haben einen Code")
        code = re.search(r"^\s*(\d{6})\s*$", mail.outbox[-1].body, re.M).group(1)
        page.fill("input[name='validation-token']", code)
        page.get_by_role("button", name="Weiter", exact=True).click()
        page.wait_for_selector("text=Zweiter Anmeldeschritt eingerichtet")
        page.goto(base + "/konto/")
        self.assertIn("Vollständig angemeldet", page.content())
        if shots:
            page.screenshot(path=f"{shots}/account.png")


@unittest.skipIf(sync_playwright is None, "BLOCKIERT: Playwright nicht installiert")
class MemberBrowserTests(StaticLiveServerTestCase):
    """Echter Browserablauf: TOTP-Login, bewusstes Oeffnen, Details/Kontakte/Fotos/Detailkarte."""

    host = "127.0.0.1"

    @classmethod
    def setUpClass(cls):
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        super().tearDownClass()

    def test_member_opens_details_with_photos_and_exact_map(self):
        from django_otp.plugins.otp_totp.models import TOTPDevice

        from . import helpers_auth as h
        from .test_stage3_member import make_published

        _, pub, _ = make_published(1)
        password = h.random_password()
        user = h.make_active_user("browser-mitglied@example.org", password)
        device = TOTPDevice.objects.create(user=user, name="default", confirmed=True)

        shots = os.environ.get("ADRESSKARTE_SCREENSHOT_DIR")
        ctx = self.browser.new_context(viewport={"width": 1100, "height": 900})
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        requests = []
        page.on("request", lambda r: requests.append(r.url))
        base = self.live_server_url

        page.goto(f"{base}/objekt/{pub.public_id}/")
        self.assertNotIn("MITGLIEDERDETAIL-", page.content())
        page.get_by_role("link", name="Anmelden").last.click()
        page.fill("input[name='auth-username']", user.email)
        page.fill("input[name='auth-password']", password)
        page.get_by_role("button", name="Weiter", exact=True).click()
        page.wait_for_selector("input[name='token-otp_token']")
        page.fill("input[name='token-otp_token']", h.totp_code(device.bin_key, 0))
        page.get_by_role("button", name="Weiter", exact=True).click()
        page.wait_for_url(f"**/objekt/{pub.public_id}/")  # Rueckkehr zum Objekt
        page.get_by_role("button", name="Details und Kontakte anzeigen").click()
        page.wait_for_selector("text=kontakt1@example.org")
        self.assertIn("MITGLIEDERDETAIL-1 lang", page.content())
        page.wait_for_selector(".leaflet-tile-loaded")
        loaded = page.eval_on_selector_all(".gallery img", "els => els.map(e => e.complete && e.naturalWidth > 0)")
        self.assertTrue(loaded and all(loaded))
        self.assertTrue(any("/map/tiles/" in u for u in requests))
        if shots:
            page.screenshot(path=f"{shots}/member_detail.png", full_page=True)
