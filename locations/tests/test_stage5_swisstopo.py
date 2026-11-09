"""Etappe 5: swisstopo-Gateway, Cache, globale Budgets, Adresssuche (Auftrag 12 E). Provider gemockt."""

import io
import json
import math
import os
import re
import shutil
import threading
import time
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from PIL import Image

from locations import rate_limits
from locations.models import TileCacheState
from locations.roles import EDITOR, OBJECT_MANAGER
from locations.services import swisstopo

from .test_stage4_work import owner_location, verified

OPEN = "locations.services.swisstopo._open"


def jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), (200, 220, 200)).save(buf, format="JPEG")
    return buf.getvalue()


JPEG = jpeg_bytes()


def tile_xy(lat=46.95, lon=7.44, z=12):
    n = 2 ** z
    return int((lon + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)


def ok_tile(*args, **kwargs):
    return 200, "image/jpeg", JPEG


class CacheIsolationMixin:
    def setUp(self):
        super().setUp()
        root = Path(settings.VAR_DIR) / settings.TILE_CACHE_DIR_NAME
        shutil.rmtree(root, ignore_errors=True)


@override_settings(MAP_TILE_SOURCE="swisstopo")
class GatewayTests(CacheIsolationMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.get("/")
        self.x, self.y = tile_xy()
        self.url = f"/map/tiles/12/{self.x}/{self.y}.jpeg"

    def test_miss_fetches_fixed_url_once_then_cache_hit(self):
        with mock.patch(OPEN, side_effect=ok_tile) as upstream:
            r1 = self.client.get(self.url + "?url=https://evil.example/&layer=x&host=evil")
            r2 = self.client.get(self.url)
        self.assertEqual((r1.status_code, r2.status_code), (200, 200))
        self.assertEqual(r1["Content-Type"], "image/jpeg")
        self.assertEqual(upstream.call_count, 1)
        called = upstream.call_args.args[0]
        self.assertEqual(called, f"https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-farbe/default/current/3857/12/{self.x}/{self.y}.jpeg")

    def test_cache_hit_does_not_consume_provider_budget(self):
        with mock.patch(OPEN, side_effect=ok_tile):
            self.client.get(self.url)
            before = rate_limits.RateEvent.objects.filter(bucket__scope="provider_tiles_global").count()
            for _ in range(5):
                self.client.get(self.url)
        self.assertEqual(rate_limits.RateEvent.objects.filter(bucket__scope="provider_tiles_global").count(), before)

    def test_invalid_upstream_responses_are_not_cached_and_count(self):
        bad = [
            lambda *a, **k: (200, "text/html", b"<html>"),
            lambda *a, **k: (200, "image/jpeg", b"GIF89a..."),
            lambda *a, **k: (200, "image/jpeg", JPEG + b"x" * settings.SWISSTOPO_TILE_MAX_BYTES),
            lambda *a, **k: (500, "image/jpeg", JPEG),
            mock.Mock(side_effect=OSError("timeout")),
        ]
        for i, behaviour in enumerate(bad):
            with self.subTest(i=i), mock.patch(OPEN, side_effect=behaviour):
                r = self.client.get(f"/map/tiles/12/{self.x}/{self.y + i}.jpeg")
                self.assertEqual(r.status_code, 503)
                self.assertIn("Retry-After", r)
                self.assertEqual(r.content, b"")
        self.assertEqual(rate_limits.RateEvent.objects.filter(bucket__scope="provider_tiles_global").count(), len(bad))
        root = Path(settings.VAR_DIR) / settings.TILE_CACHE_DIR_NAME
        self.assertEqual(list(root.rglob("*.jpg")) if root.exists() else [], [])

    @override_settings(RATE_LIMITS={**settings.RATE_LIMITS, "provider_tiles_global": [(2, 60)]})
    def test_budget_exhausted_means_zero_new_provider_calls_but_hits_served(self):
        with mock.patch(OPEN, side_effect=ok_tile) as upstream:
            self.client.get(f"/map/tiles/12/{self.x}/{self.y}.jpeg")
            self.client.get(f"/map/tiles/12/{self.x + 1}/{self.y}.jpeg")
            r = self.client.get(f"/map/tiles/12/{self.x + 2}/{self.y}.jpeg")
            hit = self.client.get(f"/map/tiles/12/{self.x}/{self.y}.jpeg")
        self.assertEqual(upstream.call_count, 2)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(hit.status_code, 200)

    def test_limiter_failure_means_no_fetch(self):
        with mock.patch(OPEN, side_effect=ok_tile) as upstream, \
                mock.patch("locations.services.swisstopo.rate_limits.reserve",
                           side_effect=rate_limits.RateLimiterUnavailable()):
            r = self.client.get(self.url)
        self.assertEqual(r.status_code, 503)
        upstream.assert_not_called()

    def test_stale_tile_is_refetched_and_not_served_when_refetch_fails(self):
        with mock.patch(OPEN, side_effect=ok_tile):
            self.client.get(self.url)
        path = swisstopo.tile_path(swisstopo.current_generation(), 12, self.x, self.y)
        old = time.time() - settings.TILE_CACHE_MAX_AGE_SECONDS - 10
        os.utime(path, (old, old))
        with mock.patch(OPEN, side_effect=OSError("down")):
            self.assertEqual(self.client.get(self.url).status_code, 503)
        with mock.patch(OPEN, side_effect=ok_tile) as upstream:
            self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(upstream.call_count, 1)

    def test_rejects_foreign_tiles_without_fetch(self):
        with mock.patch(OPEN, side_effect=ok_tile) as upstream:
            for url in ("/map/tiles/12/0/0.jpeg", "/map/tiles/3/4/2.jpeg", "/map/tiles/19/1/1.jpeg",
                        f"/map/tiles/12/{self.x}/{self.y}.png", "/map/tiles/12/../../etc.jpeg"):
                self.assertEqual(self.client.get(url).status_code, 404)
        upstream.assert_not_called()

    def test_no_session_no_upstream(self):
        with mock.patch(OPEN, side_effect=ok_tile) as upstream:
            self.assertEqual(Client().get(self.url).status_code, 403)
        upstream.assert_not_called()

    def test_cache_dirs_private_despite_umask(self):
        old = os.umask(0o022)
        try:
            with mock.patch(OPEN, side_effect=ok_tile):
                self.client.get(self.url)
        finally:
            os.umask(old)
        root = Path(settings.VAR_DIR) / settings.TILE_CACHE_DIR_NAME
        for d in [root] + [p for p in root.rglob("*") if p.is_dir()]:
            self.assertEqual(d.stat().st_mode & 0o077, 0, str(d))

    def test_cache_contains_only_basemap_tiles(self):
        with mock.patch(OPEN, side_effect=ok_tile):
            self.client.get(self.url)
        root = Path(settings.VAR_DIR) / settings.TILE_CACHE_DIR_NAME
        files = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()]
        self.assertTrue(files)
        for f in files:
            self.assertRegex(f, r"^ch\.swisstopo\.pixelkarte-farbe/\d+/\d+/\d+/\d+\.jpg$")

    def test_browser_assets_never_reference_provider_directly(self):
        for path in ("static/js/explore.js", "static/js/detail_map.js"):
            text = (Path(settings.BASE_DIR) / path).read_text()
            self.assertNotIn("geo.admin.ch", text)
            self.assertIn("/map/tiles/{z}/{x}/{y}.jpeg", text)


class GatewayUnitTests(CacheIsolationMixin, TestCase):
    def test_only_fixed_hosts_and_integer_tiles(self):
        with self.assertRaises(ValueError):
            swisstopo._open("https://evil.example/x", accept="*/*", timeout=1, max_bytes=10)
        for bad in (("1", 2, 3), (1.5, 2, 3), (-1, 2, 3), (True, 1, 1)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                swisstopo.tile_url(*bad)

    def test_redirects_are_refused(self):
        handler = swisstopo._NoRedirect()
        req = mock.Mock(full_url="https://wmts.geo.admin.ch/x")
        with self.assertRaises(Exception):
            handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example/")

    @override_settings(SWISSTOPO_MAX_CONCURRENT=1)
    def test_slots_limit_and_expire(self):
        slot, token = swisstopo._acquire_slot("tile", 30)
        with self.assertRaises(swisstopo.ProviderUnavailable):
            swisstopo._acquire_slot("tile", 30)
        swisstopo._release_slot("tile", slot, token)
        slot, token = swisstopo._acquire_slot("tile", -1)  # sofort abgelaufen
        swisstopo._acquire_slot("tile", 30)  # abgelaufene Lease uebernehmbar

    def test_fetch_lock_dedup_and_takeover(self):
        first = swisstopo._acquire_fetch_lock("k", 30)
        self.assertIsNotNone(first)
        self.assertIsNone(swisstopo._acquire_fetch_lock("k", 30))
        swisstopo._release_fetch_lock("k", first)
        self.assertIsNotNone(swisstopo._acquire_fetch_lock("k", -1))
        self.assertIsNotNone(swisstopo._acquire_fetch_lock("k", 30))

    def test_cache_update_changes_generation_and_counts_rest_budget(self):
        payload = lambda value: (lambda *a, **k: (200, "application/json", json.dumps({"cache_type": "wmts_native", "cache_update": value}).encode()))
        with mock.patch(OPEN, side_effect=payload("2026-11-01T08:00:00")) as upstream:
            call_command("check_tile_cache_update", stdout=io.StringIO())
        self.assertIn("/rest/services/ech/MapServer/ch.swisstopo.pixelkarte-farbe/cacheUpdate", upstream.call_args.args[0])
        gen = TileCacheState.objects.get().generation
        with mock.patch(OPEN, side_effect=payload("2026-11-01T08:00:00")):
            call_command("check_tile_cache_update", stdout=io.StringIO())
        self.assertEqual(TileCacheState.objects.get().generation, gen)
        with mock.patch(OPEN, side_effect=payload("2026-11-09T08:00:00")):
            call_command("check_tile_cache_update", stdout=io.StringIO())
        self.assertEqual(TileCacheState.objects.get().generation, gen + 1)
        self.assertEqual(rate_limits.RateEvent.objects.filter(bucket__scope="provider_rest_global").count(), 3)
        with mock.patch(OPEN, side_effect=lambda *a, **k: (200, "application/json", b"{\"cache_update\": \"<script>\"}")):
            with self.assertRaises(CommandError):
                call_command("check_tile_cache_update", stdout=io.StringIO())

    @override_settings(TILE_CACHE_MAX_BYTES=len(JPEG) * 2)
    def test_cleanup_removes_old_generations_and_bounds_size(self):
        gen = swisstopo.current_generation()
        for i in range(4):
            path = swisstopo.tile_path(gen, 12, 1, i)
            swisstopo._write_atomic(path, JPEG)
            os.utime(path, (time.time() - 100 + i, time.time() - 100 + i))
        swisstopo._write_atomic(swisstopo.tile_path(gen + 5, 12, 1, 1), JPEG)
        result = swisstopo.cleanup_cache()
        self.assertEqual(result["generations"], 1)
        self.assertEqual(result["evicted"], 2)
        self.assertTrue(swisstopo.tile_path(gen, 12, 1, 3).exists())  # neueste bleiben
        self.assertFalse(swisstopo.tile_path(gen, 12, 1, 0).exists())


@override_settings(MAP_TILE_SOURCE="swisstopo")
class GatewayConcurrencyTests(CacheIsolationMixin, TransactionTestCase):
    def _run(self, urls, upstream_impl):
        results, barrier = [], threading.Barrier(len(urls))

        def worker(url):
            c = Client()
            try:
                c.get("/")
                barrier.wait()
                results.append(c.get(url).status_code)
            finally:
                connection.close()

        with mock.patch(OPEN, side_effect=upstream_impl):
            threads = [threading.Thread(target=worker, args=(u,)) for u in urls]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        return results

    def test_parallel_same_miss_fetches_once(self):
        calls = []

        def slow(*a, **k):
            calls.append(1)
            time.sleep(0.4)
            return ok_tile()

        x, y = tile_xy()
        results = self._run([f"/map/tiles/12/{x}/{y}.jpeg"] * 5, slow)
        self.assertEqual(len(calls), 1)
        self.assertEqual(sorted(results), [200] * 5)

    @override_settings(SWISSTOPO_MAX_CONCURRENT=2)
    def test_concurrent_provider_calls_are_capped(self):
        active, peak, lock = [0], [0], threading.Lock()

        def slow(*a, **k):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.4)
            with lock:
                active[0] -= 1
            return ok_tile()

        x, y = tile_xy()
        results = self._run([f"/map/tiles/12/{x + i}/{y}.jpeg" for i in range(6)], slow)
        self.assertLessEqual(peak[0], 2)
        self.assertEqual(set(results) - {200, 503}, set())

    @override_settings(RATE_LIMITS={**settings.RATE_LIMITS, "provider_tiles_global": [(3, 60)]})
    def test_global_budget_holds_under_parallel_load(self):
        calls = []

        def counting(*a, **k):
            calls.append(1)
            return ok_tile()

        x, y = tile_xy()
        self._run([f"/map/tiles/12/{x + i}/{y + 1}.jpeg" for i in range(8)], counting)
        self.assertLessEqual(len(calls), 3)


SEARCH_RESPONSE = {"results": [
    {"attrs": {"label": "<b>Bundesplatz 3</b> 3011 Bern", "lat": 46.9466, "lon": 7.4440}},
    {"attrs": {"label": "Ausserhalb", "lat": 52.5, "lon": 13.4}},
    {"attrs": {"label": "Kaputt", "lat": "NaN", "lon": 7.4}},
] + [{"attrs": {"label": f"Treffer {i}", "lat": 46.9 + i / 100, "lon": 7.4}} for i in range(10)]}


def search_ok(*a, **k):
    return 200, "application/json; charset=utf-8", json.dumps(SEARCH_RESPONSE).encode()


class GeocodeTests(TestCase):
    def setUp(self):
        self.owner, self.owner_user = verified("geo-o@example.org", groups=[OBJECT_MANAGER])
        self.loc = owner_location(self.owner_user)
        self.url = reverse("portal:geocode", args=[self.loc.public_id])

    def test_owner_gets_validated_results_with_fixed_parameters(self):
        with mock.patch(OPEN, side_effect=search_ok) as upstream, self.assertNoLogs("locations", level="INFO"):
            r = self.owner.post(self.url, {"q": "Bundesplatz 3 Bern&type=x&sr=21781"})
        self.assertEqual(r.status_code, 200)
        data = r.json()["results"]
        self.assertEqual(data[0]["label"], "Bundesplatz 3 3011 Bern")
        self.assertEqual(len(data), settings.GEOCODE_MAX_RESULTS)
        self.assertTrue(all(45.8 <= d["lat"] <= 47.85 for d in data))
        called = upstream.call_args.args[0]
        self.assertTrue(called.startswith("https://api3.geo.admin.ch/rest/services/ech/SearchServer?"))
        self.assertIn("type=locations", called)
        self.assertIn("origins=address", called)
        self.assertIn("sr=4326", called)
        self.assertIn("limit=5", called)
        self.assertEqual(called.count("type="), 1)

    def test_access_rules(self):
        visitor, _ = verified("geo-v@example.org")
        other, other_user = verified("geo-b@example.org", groups=[OBJECT_MANAGER])
        editor, _ = verified("geo-r@example.org", groups=[EDITOR], staff=True)
        with mock.patch(OPEN, side_effect=search_ok) as upstream:
            self.assertEqual(visitor.post(self.url, {"q": "Bern Bahnhof"}).status_code, 404)
            self.assertEqual(other.post(self.url, {"q": "Bern Bahnhof"}).status_code, 404)
            self.assertEqual(Client().post(self.url, {"q": "Bern Bahnhof"}).status_code, 404)
            self.assertEqual(visitor.post(reverse("admin_geocode"), {"q": "Bern Bahnhof"}).status_code, 404)
            self.assertEqual(self.owner.get(self.url).status_code, 405)
            self.assertEqual(upstream.call_count, 0)
            self.assertEqual(editor.post(reverse("admin_geocode"), {"q": "Bern Bahnhof"}).status_code, 200)

    def test_invalid_text_no_provider_call(self):
        with mock.patch(OPEN, side_effect=search_ok) as upstream:
            for q in ("", "ab", "x" * 101, " ".join(["w"] * 11)):
                self.assertEqual(self.owner.post(self.url, {"q": q}).status_code, 400)
        upstream.assert_not_called()

    def test_per_account_and_global_rest_budget(self):
        with mock.patch(OPEN, side_effect=search_ok) as upstream:
            statuses = [self.owner.post(self.url, {"q": f"Bern {i}"}).status_code for i in range(7)]
        self.assertEqual(statuses, [200] * 6 + [429])
        self.assertEqual(upstream.call_count, 6)
        with override_settings(RATE_LIMITS={**settings.RATE_LIMITS, "provider_rest_global": [(6, 60)]}):
            other, other_user = verified("geo-c@example.org", groups=[OBJECT_MANAGER])
            loc = owner_location(other_user)
            with mock.patch(OPEN, side_effect=search_ok) as upstream:
                r = other.post(reverse("portal:geocode", args=[loc.public_id]), {"q": "Zuerich HB"})
            self.assertEqual(r.status_code, 503)
            upstream.assert_not_called()

    def test_provider_failure_is_reported_without_fallback(self):
        with mock.patch(OPEN, side_effect=OSError("down")):
            r = self.owner.post(self.url, {"q": "Bern Bahnhof"})
        self.assertEqual(r.status_code, 503)
        self.assertIn("manuell", r.json()["error"])
