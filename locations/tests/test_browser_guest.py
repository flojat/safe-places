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
        call_command("seed_demo", "--count", "100", stdout=StringIO())

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
        page.wait_for_selector("#results li")
        page.wait_for_selector(".leaflet-interactive")
        self.assertEqual(page.locator("#results li").count(), 10)
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
        page.locator("#results li").first.click(position={"x": 5, "y": 40})
        self.assertEqual(page.locator("#results li.active").count(), 1)

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
        page.wait_for_selector("#results li")
        html = page.content()
        for marker in ("CANARY-", "MITGLIEDERDETAIL-", "@example.org"):
            self.assertNotIn(marker, html)
        page.locator("#results li h2 a").first.click()
        page.wait_for_selector("article.preview")
        self.assertIn("nur für angemeldete Mitglieder", page.content())
        self.assertNotIn("MITGLIEDERDETAIL-", page.content())

    def test_mobile_toggle_loads_map_only_when_shown(self):
        page, requests = self._page(viewport={"width": 390, "height": 800}, is_mobile=True)
        page.goto(self.live_server_url + "/")
        page.wait_for_selector("#results li")
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
