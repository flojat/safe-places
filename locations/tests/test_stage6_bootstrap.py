"""Etappe 6: Initialkonten, Einladung, Aktivierung (Auftrag 12 H). Nur synthetische Adressen."""

import json
import re
import tempfile
import threading
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django_otp.plugins.otp_email.models import EmailDevice

from locations import bootstrap
from locations.models import InitialAccount
from locations.roles import EDITOR, OBJECT_MANAGER

from . import helpers_auth as h

User = get_user_model()
ACCOUNTS = [
    {"email": "besucher@example.org", "role": "visitor"},
    {"email": "objektverwalter@example.org", "role": "object_manager"},
    {"email": "redaktor@example.org", "role": "editor"},
    {"email": "administrator@example.org", "role": "admin"},
]
BASE = "http://127.0.0.1"


def manifest_file(accounts=ACCOUNTS) -> str:
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"accounts": accounts}, tmp)
    tmp.close()
    return tmp.name


def provision(*extra, accounts=ACCOUNTS):
    out = StringIO()
    call_command("provision_initial_accounts", "--manifest", manifest_file(accounts),
                 "--environment", "local", *extra, stdout=out)
    return out.getvalue()


def invite(role=None):
    out = StringIO()
    args = ["--environment", "local", "--base-url", BASE, "--apply"]
    if role:
        args += ["--role", role]
    call_command("send_initial_invitations", *args, stdout=out)
    return out.getvalue()


def invitation_path(email):
    body = [m for m in mail.outbox if m.to == [email]][-1].body
    link = re.search(r"https?://\S+", body).group(0)
    return "/" + link.split("://", 1)[1].split("/", 1)[1]


def activate(client, email, password):
    client.get(invitation_path(email))
    return client.post(reverse("account:activate"), {"password1": password, "password2": password})


@override_settings(ENVIRONMENT="local")
class ProvisioningTests(TestCase):
    def test_plan_changes_nothing_and_shows_no_addresses(self):
        out = provision()
        self.assertIn("PLAN", out)
        self.assertNotIn("@", out)
        self.assertFalse(User.objects.exists())

    def test_apply_creates_pending_accounts_without_password_or_rights(self):
        out = provision("--apply")
        self.assertNotIn("@", out)
        self.assertEqual(User.objects.count(), 4)
        for user in User.objects.all():
            self.assertEqual(user.state, User.State.PENDING_ACTIVATION)
            self.assertFalse(user.has_usable_password())
            self.assertIsNone(user.email_verified_at)
            self.assertFalse(user.is_staff or user.is_superuser or user.groups.exists())
        self.assertEqual(len(mail.outbox), 0)  # Provisionierung versendet nichts

    def test_repeated_apply_changes_nothing(self):
        provision("--apply")
        admin = User.objects.get(email="administrator@example.org")
        before = (admin.password, admin.security_generation, admin.state)
        User.objects.filter(pk=admin.pk).update(state=User.State.SUSPENDED, is_active=False)
        out = provision("--apply")
        self.assertEqual(out.count("unchanged"), 4)
        admin.refresh_from_db()
        self.assertEqual(admin.state, User.State.SUSPENDED)  # nicht entsperrt
        self.assertEqual(admin.password, before[0])
        self.assertEqual(User.objects.count(), 4)

    def test_existing_registration_is_conflict_without_privileges(self):
        pw = h.random_password()
        h.register(self.client, "ADMINISTRATOR@example.org", pw)  # Fremdregistrierung, andere Schreibweise
        out = provision("--apply")
        self.assertIn("conflict", out)
        user = User.objects.get(email="administrator@example.org")
        self.assertFalse(user.is_superuser or user.is_staff)
        self.assertEqual(InitialAccount.objects.get(email="administrator@example.org").state, "conflict")
        self.assertTrue(user.check_password(pw))  # nicht zurueckgesetzt

    def test_deleted_account_is_not_restored(self):
        provision("--apply")
        User.objects.filter(email="besucher@example.org").delete()
        out = provision("--apply")
        self.assertIn("removed", out)
        self.assertFalse(User.objects.filter(email="besucher@example.org").exists())

    def test_manifest_validation_does_not_echo_addresses(self):
        bad_sets = [
            ACCOUNTS[:3],
            ACCOUNTS[:3] + [{"email": "x@example.org", "role": "visitor"}],
            ACCOUNTS[:3] + [{"email": "geheim@example.org", "role": "superuser"}],
            ACCOUNTS[:3] + [{"email": "geheim@example.org", "role": "admin", "password": "x"}],
        ]
        for accounts in bad_sets:
            with self.subTest(n=len(accounts)), self.assertRaises(CommandError) as ctx:
                provision("--apply", accounts=accounts)
            self.assertNotIn("@", str(ctx.exception))
        self.assertFalse(User.objects.exists())

    def test_environment_mismatch_refused(self):
        with self.assertRaises(CommandError):
            call_command("provision_initial_accounts", "--manifest", manifest_file(),
                         "--environment", "production", "--apply", stdout=StringIO())

    def test_shareable_status_report_has_no_addresses(self):
        provision("--apply")
        report = json.dumps(bootstrap.status_report())
        self.assertNotIn("@", report)
        self.assertEqual(set(bootstrap.status_report()), {"visitor", "object_manager", "editor", "admin"})


@override_settings(ENVIRONMENT="local")
class ActivationTests(TestCase):
    def setUp(self):
        provision("--apply")

    def test_invitation_get_does_not_consume_and_post_activates_once(self):
        invite("admin")
        email = "administrator@example.org"
        path = invitation_path(email)
        scanner = Client()
        r = scanner.get(path, follow=True)
        self.assertContains(r, "Konto aktivieren")
        self.assertEqual(User.objects.get(email=email).state, User.State.PENDING_ACTIVATION)
        pw = h.random_password()
        r = activate(Client(), email, pw)
        self.assertContains(r, "Konto aktiviert")
        user = User.objects.get(email=email)
        self.assertTrue(user.check_password(pw))
        self.assertIsNotNone(user.email_verified_at)
        self.assertFalse(user.is_superuser)  # Rolle erst nach zweitem Schritt
        # Replay und paralleler Gebrauch scheitern
        again = Client()
        again.get(path)
        self.assertEqual(again.post(reverse("account:activate"), {"password1": pw, "password2": pw}).status_code, 400)

    def test_new_invitation_invalidates_previous(self):
        invite("editor")
        old = invitation_path("redaktor@example.org")
        mail.outbox.clear()
        from locations import rate_limits
        rate_limits.RateEvent.objects.filter(bucket__scope="mail_purpose_recipient").delete()
        invite("editor")
        c = Client()
        c.get(old)
        self.assertEqual(c.get(reverse("account:activate")).status_code, 400)
        self.assertEqual(activate(Client(), "redaktor@example.org", h.random_password()).status_code, 200)

    def test_invitation_not_usable_as_verification_reset_or_other_env(self):
        invite("visitor")
        path = invitation_path("besucher@example.org")
        token = path.rstrip("/").rsplit("/", 1)[1]
        c = Client()
        c.get(reverse("account:verify_email_token", args=[token]))
        self.assertEqual(c.post(reverse("account:verify_email")).status_code, 400)
        with override_settings(ENVIRONMENT="staging"):
            c2 = Client()
            c2.get(path)
            self.assertEqual(c2.get(reverse("account:activate")).status_code, 400)

    def test_password_reset_cannot_bypass_activation(self):
        mail.outbox.clear()
        Client().post(reverse("account:password_reset"), {"email": "administrator@example.org"})
        self.assertEqual(len(mail.outbox), 0)

    def test_unactivated_account_cannot_log_in(self):
        r = h.login_password(self.client, "administrator@example.org", "irgendwas-langes-123")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_smtp_failure_keeps_pending_without_fallback(self):
        with mock.patch("django.core.mail.backends.locmem.EmailBackend.send_messages", side_effect=OSError):
            out = invite("admin")
        self.assertIn("NICHT gesendet", out)
        user = User.objects.get(email="administrator@example.org")
        self.assertEqual(user.state, User.State.PENDING_ACTIVATION)
        self.assertFalse(user.has_usable_password())

    def test_production_send_requires_confirmation(self):
        with override_settings(ENVIRONMENT="production"):
            with self.assertRaises(CommandError):
                call_command("send_initial_invitations", "--environment", "production",
                             "--base-url", "https://example.org", "--apply", stdout=StringIO())

    def _activate_and_login(self, email):
        invite()
        pw = h.random_password()
        activate(Client(), email, pw)
        client = Client()
        r = h.login_password(client, email, pw)
        self.assertRedirects(r, reverse("two_factor:setup"), fetch_redirect_response=False)
        return client

    def test_admin_gets_role_only_after_totp_setup(self):
        client = self._activate_and_login("administrator@example.org")
        self.assertNotEqual(client.get("/verwaltung/").status_code, 200)
        self.assertFalse(User.objects.get(email="administrator@example.org").is_superuser)
        h.setup_totp(client)
        user = User.objects.get(email="administrator@example.org")
        self.assertTrue(user.is_superuser and user.is_staff)
        self.assertEqual(client.get("/verwaltung/").status_code, 200)
        self.assertEqual(InitialAccount.objects.get(user=user).state, "complete")

    def test_editor_and_manager_can_choose_email_code(self):
        for email, group in (("redaktor@example.org", EDITOR), ("objektverwalter@example.org", OBJECT_MANAGER)):
            with self.subTest(email=email):
                mail.outbox.clear()
                from locations import rate_limits
                rate_limits.RateEvent.objects.filter(bucket__scope__startswith="mail_").delete()
                client = self._activate_and_login(email)
                self.assertEqual(h.setup_email(client).status_code, 302)
                user = User.objects.get(email=email)
                self.assertTrue(user.groups.filter(name=group).exists())
                self.assertTrue(EmailDevice.objects.filter(user=user).exists())

    def test_visitor_gets_no_staff(self):
        client = self._activate_and_login("besucher@example.org")
        h.setup_totp(client)
        user = User.objects.get(email="besucher@example.org")
        self.assertFalse(user.is_staff or user.groups.exists())
        self.assertTrue(h.is_fully_verified(client))

    def test_no_email_fallback_forced_for_totp_users(self):
        client = self._activate_and_login("redaktor@example.org")
        h.setup_totp(client)
        self.assertFalse(EmailDevice.objects.filter(user__email="redaktor@example.org").exists())


@override_settings(ENVIRONMENT="local", REGISTRATION_REQUIRES_ADMIN=True)
class RegistrationGateTests(TestCase):
    def test_registration_closed_until_admin_active(self):
        self.assertEqual(self.client.get(reverse("account:register")).status_code, 503)
        provision("--apply")
        invite("admin")
        pw = h.random_password()
        activate(Client(), "administrator@example.org", pw)
        c = Client()
        h.login_password(c, "administrator@example.org", pw)
        self.assertEqual(self.client.get(reverse("account:register")).status_code, 503)  # noch ohne Faktor
        h.setup_totp(c)
        self.assertEqual(self.client.get(reverse("account:register")).status_code, 200)


@override_settings(ENVIRONMENT="local")
class ParallelProvisioningTests(TransactionTestCase):
    def test_parallel_apply_creates_no_duplicates(self):
        path = manifest_file()
        barrier = threading.Barrier(4)

        def worker():
            try:
                barrier.wait()
                call_command("provision_initial_accounts", "--manifest", path, "--environment", "local",
                             "--apply", stdout=StringIO())
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(User.objects.count(), 4)
        self.assertEqual(InitialAccount.objects.count(), 4)
