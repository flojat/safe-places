"""Etappe 7: Zusatzfaktor, Faktorentfernung, Benachrichtigungen, Admin-Wiederherstellung,
Routenpruefung auf Umgehungen (Auftrag 12 B/F)."""

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django_otp.plugins.otp_email.models import EmailDevice
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from locations.models import AuditEvent

from . import helpers_auth as h
from .test_stage4_work import backup_code, reauth, set_known_password, verified

User = get_user_model()


class AdditionalFactorTests(TestCase):
    def setUp(self):
        self.client_, self.user = verified("zusatz@example.org")  # TOTP

    def _post(self, step, **fields):
        data = {"add_factor_view-current_step": step, **{f"{step}-{k}": v for k, v in fields.items()}}
        return self.client_.post(reverse("two_factor:add_factor"), data)

    def test_regular_setup_cannot_add_second_factor_without_reauth(self):
        r = h.setup_post(self.client_, "welcome")
        self.assertEqual(r.status_code, 302)
        h.setup_post(self.client_, "method", method="generator")
        self.assertEqual(TOTPDevice.objects.filter(user=self.user).count(), 1)
        self.assertFalse(EmailDevice.objects.filter(user=self.user).exists())

    def test_requires_reauth_and_offers_only_missing_method(self):
        url = reverse("two_factor:add_factor")
        self.assertIn(reverse("account:reauth"), self.client_.get(url)["Location"])
        reauth(self.client_, self.user)
        self.client_.get(url)
        r = self._post("welcome")
        # Nur E-Mail fehlt -> Methodenauswahl entfaellt bzw. enthaelt nur email
        step = r.context["wizard"]["steps"].current
        self.assertIn(step, ("method", "validation"))
        if step == "method":
            self.assertEqual(set(dict(r.context["wizard"]["form"].fields["method"].choices)), {"email"})
            self._post("method", method="email")
        mail_count = len(mail.outbox)
        r = self._post("validation", token=h.last_code())
        self.assertEqual(r.status_code, 302)
        self.assertTrue(EmailDevice.objects.filter(user=self.user, confirmed=True).exists())
        self.assertTrue(TOTPDevice.objects.filter(user=self.user).exists())
        self.assertTrue(any("Sicherheitshinweis" in m.subject for m in mail.outbox[mail_count:]))

    def test_login_offers_alternative_only_when_explicitly_added(self):
        pw = set_known_password(self.client_, self.user)
        c = Client()
        r = h.login_password(c, self.user.email, pw)
        self.assertNotContains(r, "Stattdessen E-Mail-Code")
        EmailDevice.objects.create(user=self.user, name="email", confirmed=True)
        c2 = Client()
        r = h.login_password(c2, self.user.email, pw)
        self.assertContains(r, "Stattdessen E-Mail-Code")

    def test_last_factor_cannot_be_removed_and_other_can(self):
        reauth(self.client_, self.user)
        r = self.client_.post(reverse("account:remove_factor"), {"kind": "generator"}, follow=True)
        self.assertContains(r, "letzte Anmeldeschritt")
        self.assertTrue(TOTPDevice.objects.filter(user=self.user).exists())
        EmailDevice.objects.create(user=self.user, name="email", confirmed=True)
        reauth(self.client_, self.user)
        mail.outbox.clear()
        self.client_.post(reverse("account:remove_factor"), {"kind": "generator"})
        self.assertFalse(TOTPDevice.objects.filter(user=self.user).exists())
        self.assertNotIn("_auth_user_id", self.client_.session)  # neu anmelden
        self.assertTrue(any("entfernt" in m.subject for m in mail.outbox))

    def test_remove_requires_reauth(self):
        EmailDevice.objects.create(user=self.user, name="email", confirmed=True)
        r = self.client_.post(reverse("account:remove_factor"), {"kind": "generator"})
        self.assertIn(reverse("account:reauth"), r["Location"])
        self.assertTrue(TOTPDevice.objects.filter(user=self.user).exists())


class BackupCodeTests(TestCase):
    def test_backup_code_login_is_single_use_logged_and_notified(self):
        client, user = verified("backup@example.org")
        pw = set_known_password(client, user)
        code = backup_code(user)
        mail.outbox.clear()
        c = Client()
        h.login_password(c, user.email, pw)
        c.post(reverse("two_factor:login"), {f"{h.LOGIN}-current_step": "token", "wizard_goto_step": "backup"})
        r = c.post(reverse("two_factor:login"), {f"{h.LOGIN}-current_step": "backup", "backup-otp_token": code})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(h.is_fully_verified(c))
        self.assertTrue(AuditEvent.objects.filter(action="backup_code_used", target_id=str(user.pk)).exists())
        self.assertTrue(any("Wiederherstellungscode" in m.subject for m in mail.outbox))
        self.assertNotIn(code, "".join(m.body + m.subject for m in mail.outbox))
        c3 = Client()
        h.login_password(c3, user.email, pw)
        c3.post(reverse("two_factor:login"), {f"{h.LOGIN}-current_step": "token", "wizard_goto_step": "backup"})
        r = c3.post(reverse("two_factor:login"), {f"{h.LOGIN}-current_step": "backup", "backup-otp_token": code})
        self.assertNotEqual(r.status_code, 302)


class AdminRecoveryTests(TestCase):
    def setUp(self):
        self.admin, self.admin_user = verified("admin-r@example.org", superuser=True)
        self.target_client, self.target = verified("verloren@example.org", groups=["Redaktion"], staff=True)
        backup_code(self.target)

    def test_recovery_requires_reauth_identity_check_and_resets_factors_only(self):
        url = reverse("admin:locations_user_recovery", args=[self.target.pk])
        self.assertEqual(self.admin.get(url).status_code, 200)
        r = self.admin.post(url, {"identity_checked": "on"})
        self.assertIn(reverse("account:reauth"), r["Location"])
        reauth(self.admin, self.admin_user)
        self.admin.post(url, {})  # ohne Identitaetsbestaetigung: nichts
        self.assertTrue(TOTPDevice.objects.filter(user=self.target).exists())
        mail.outbox.clear()
        old_password = User.objects.get(pk=self.target.pk).password
        self.admin.post(url, {"identity_checked": "on"})
        self.target.refresh_from_db()
        self.assertFalse(TOTPDevice.objects.filter(user=self.target).exists())
        self.assertFalse(StaticDevice.objects.filter(user=self.target).exists())
        self.assertEqual(self.target.state, User.State.PENDING_EMAIL)
        self.assertEqual(self.target.password, old_password)  # Admin setzt/kennt kein Passwort
        self.assertTrue(self.target.groups.filter(name="Redaktion").exists())
        subjects = [m.subject for m in mail.outbox]
        self.assertTrue(any("Neueinrichtung" in s for s in subjects))
        self.assertTrue(any("bestätigen" in s for s in subjects))
        # Bestehende Sitzung hat keinen Zugang mehr
        self.target_client.get("/")
        self.assertNotEqual(self.target_client.get("/verwaltung/").status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(action="admin_recovery").exists())

    def test_editor_cannot_use_recovery(self):
        editor, _ = verified("ed2@example.org", groups=["Redaktion"], staff=True)
        r = editor.get(reverse("admin:locations_user_recovery", args=[self.target.pk]))
        self.assertNotEqual(r.status_code, 200)


PUBLIC_ROUTES = {
    "locations:home", "locations:api_points", "locations:api_list", "locations:preview",
    "locations:photo", "locations:tile",
    "two_factor:login", "account:register", "account:verify_email", "account:verify_email_token",
    "account:resend_verification", "account:password_reset", "account:password_reset_done",
    "account:password_reset_confirm", "account:password_reset_complete", "account:activate",
    "account:activate_token", "account:email_change_confirm", "account:email_change_token",
    "account:logout",
}


def _all_route_names():
    names = []

    def walk(patterns, ns=""):
        for p in patterns:
            if isinstance(p, URLResolver):
                if p.app_name == "admin":
                    continue
                walk(p.url_patterns, f"{ns}{p.namespace}:" if p.namespace else ns)
            elif isinstance(p, URLPattern) and p.name:
                names.append((f"{ns}{p.name}", p))

    walk(get_resolver().url_patterns)
    return names


def _url_for(name, pattern):
    args = []
    for conv in pattern.pattern.converters.values():
        args.append(1 if conv.regex == "[0-9]+" else "x1")
    try:
        return reverse(name, args=args)
    except Exception:
        return None


class RouteBypassTests(TestCase):
    """Jeder nicht ausdruecklich oeffentliche Weg liefert Anonymen und Passwort-only-Sitzungen
    keine 200-Antwort (GET und POST)."""

    def test_no_protected_route_answers_200_without_full_verification(self):
        pw = h.random_password()
        user = h.make_active_user("halb-r@example.org", pw)
        user.is_staff = user.is_superuser = True
        user.save()
        half = Client()
        h.login_password(half, user.email, pw)  # Passwort ok, kein zweiter Schritt
        clients = {"anonym": Client(), "passwort-only": half}
        checked = 0
        for name, pattern in _all_route_names():
            if name in PUBLIC_ROUTES or name in {"two_factor:setup", "two_factor:qr", "account:home"}:
                continue
            url = _url_for(name, pattern)
            if url is None:
                continue
            for label, client in clients.items():
                for method in ("get", "post"):
                    with self.subTest(route=name, client=label, method=method):
                        r = getattr(client, method)(url)
                        self.assertNotEqual(r.status_code, 200, f"{name} {method} {label}")
                        checked += 1
        self.assertGreater(checked, 40)
        for path in ("/verwaltung/", "/verwaltung/locations/location/", "/verwaltung/locations/user/",
                     "/verwaltung/adresssuche/"):
            for label, client in clients.items():
                with self.subTest(path=path, client=label):
                    self.assertNotEqual(client.get(path).status_code, 200)

    def test_setup_area_for_password_only_exposes_no_member_data(self):
        pw = h.random_password()
        user = h.make_active_user("halb2@example.org", pw)
        c = Client()
        h.login_password(c, user.email, pw)
        home = c.get(reverse("account:home"))
        self.assertEqual(home.context["state"], "password_verified_mfa_pending")
        self.assertNotContains(home, "Wiederherstellungscodes</a>")
