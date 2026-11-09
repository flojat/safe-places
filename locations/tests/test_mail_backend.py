"""SMTP-TLS-Profile: Pruefung bleibt immer aktiv; nur dokumentierte Abweichungen."""

import ssl

from django.test import SimpleTestCase, override_settings

from locations.mail_backend import EmailBackend, build_context


class MailTlsProfileTests(SimpleTestCase):
    def test_verification_always_enabled(self):
        for profile in ("default", "no_dhe", "legacy_dh"):
            with self.subTest(profile=profile):
                ctx = build_context(profile)
                self.assertTrue(ctx.check_hostname)
                self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
                self.assertGreaterEqual(ctx.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_no_dhe_offers_no_classic_dhe(self):
        names = [c["name"] for c in build_context("no_dhe").get_ciphers()]
        tls12 = [n for n in names if not n.startswith("TLS_")]
        self.assertTrue(tls12)
        self.assertFalse([n for n in tls12 if n.startswith("DHE-")])
        self.assertTrue(all(n.startswith("ECDHE-") for n in tls12))

    def test_unknown_profile_rejected(self):
        with self.assertRaises(ValueError):
            build_context("none")

    @override_settings(EMAIL_TLS_PROFILE="no_dhe", EMAIL_TLS_CAFILE="")
    def test_backend_uses_profile(self):
        ctx = EmailBackend(host="mx.example.org", port=587, use_tls=True).ssl_context
        self.assertTrue(ctx.check_hostname)
        names = [c["name"] for c in ctx.get_ciphers() if not c["name"].startswith("TLS_")]
        self.assertFalse([n for n in names if n.startswith("DHE-")])
