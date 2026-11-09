"""Etappe 2: Registrierung, E-Mail-Bestaetigung, Login mit E-Mail-Code ODER TOTP (Auftrag 12 B)."""

import threading
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django_otp.plugins.otp_email.models import EmailDevice
from django_otp.plugins.otp_totp.models import TOTPDevice

from locations import rate_limits
from locations.models import LoginChallenge
from locations.permissions import FULLY_VERIFIED, MFA_PENDING

from . import helpers_auth as h

User = get_user_model()
PROTECTED = "two_factor:backup_tokens"  # Beispiel fuer fully_verified_required


class RegistrationTests(TestCase):
    def test_registration_creates_pending_visitor_without_roles(self):
        pw = h.random_password()
        r = h.register(self.client, "neu@example.org", pw)
        self.assertContains(r, "Bitte E-Mail prüfen")
        user = User.objects.get(email="neu@example.org")
        self.assertEqual(user.state, User.State.PENDING_EMAIL)
        self.assertFalse(user.is_staff or user.is_superuser or user.groups.exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn("neu@example.org", mail.outbox[0].subject)

    def test_role_fields_in_post_are_ignored(self):
        pw = h.random_password()
        self.client.post(reverse("account:register"), {
            "email": "x@example.org", "password1": pw, "password2": pw,
            "is_staff": "on", "is_superuser": "on", "state": "active", "groups": "1",
        })
        user = User.objects.get(email="x@example.org")
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertEqual(user.state, User.State.PENDING_EMAIL)

    def test_existing_address_gets_same_response_and_is_not_taken_over(self):
        old_pw = h.random_password()
        existing = h.make_active_user("da@example.org", old_pw)
        r = h.register(self.client, "DA@example.org", h.random_password())
        self.assertContains(r, "Bitte E-Mail prüfen")
        existing.refresh_from_db()
        self.assertTrue(existing.check_password(old_pw))
        self.assertEqual(User.objects.filter(email="da@example.org").count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_weak_password_rejected(self):
        r = h.register(self.client, "w@example.org", "12345678")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(email="w@example.org").exists())

    def test_ip_registration_limit(self):
        for i in range(3):
            h.register(self.client, f"r{i}@example.org", h.random_password())
        r = h.register(self.client, "r9@example.org", h.random_password())
        self.assertEqual(r.status_code, 429)
        self.assertFalse(User.objects.filter(email="r9@example.org").exists())

    def test_csrf_is_enforced(self):
        client = Client(enforce_csrf_checks=True)
        pw = h.random_password()
        r = client.post(reverse("account:register"), {"email": "c@example.org", "password1": pw, "password2": pw})
        self.assertEqual(r.status_code, 403)


class VerificationTests(TestCase):
    def setUp(self):
        self.pw = h.random_password()
        h.register(self.client, "v@example.org", self.pw)
        self.user = User.objects.get(email="v@example.org")

    def test_get_does_not_consume_and_post_does(self):
        path = "/" + h.last_link().split("://", 1)[1].split("/", 1)[1]
        first = self.client.get(path)
        self.assertEqual(first["Referrer-Policy"], "no-referrer")
        self.assertEqual(first["Location"], reverse("account:verify_email"))
        r = self.client.get(first["Location"])
        self.assertContains(r, "E-Mail-Adresse bestätigen")
        self.assertEqual(r["Referrer-Policy"], "same-origin")
        self.assertNotIn(path.rsplit("/", 2)[-2], r.content.decode())  # Token nicht im HTML
        self.user.refresh_from_db()
        self.assertEqual(self.user.state, User.State.PENDING_EMAIL)
        self.client.post(reverse("account:verify_email"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.state, User.State.ACTIVE)
        self.assertIsNotNone(self.user.email_verified_at)

    def test_verification_is_single_use(self):
        link_path = "/" + h.last_link().split("://", 1)[1].split("/", 1)[1]
        h.verify_from_mail(self.client)
        other = Client()
        other.get(link_path)
        r = other.post(reverse("account:verify_email"))
        self.assertEqual(r.status_code, 400)

    def test_verification_creates_no_session(self):
        h.verify_from_mail(self.client)
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(EMAIL_VERIFICATION_MAX_AGE=-1)
    def test_expired_token_rejected(self):
        r = h.verify_from_mail(self.client)
        self.assertEqual(r.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.state, User.State.PENDING_EMAIL)

    def test_token_bound_to_current_address(self):
        User.objects.filter(pk=self.user.pk).update(email="anders@example.org")
        r = h.verify_from_mail(self.client)
        self.assertEqual(r.status_code, 400)

    def test_token_bound_to_environment(self):
        with override_settings(ENVIRONMENT="staging"):
            r = h.verify_from_mail(self.client)
        self.assertEqual(r.status_code, 400)

    def test_tampered_token_rejected(self):
        self.client.get(reverse("account:verify_email_token", args=["abc:def:ghi"]))
        self.assertEqual(self.client.post(reverse("account:verify_email")).status_code, 400)

    def test_unverified_account_cannot_log_in(self):
        r = h.login_password(self.client, "v@example.org", self.pw)
        self.assertContains(r, "noch nicht aktiviert")
        self.assertNotIn("_auth_user_id", self.client.session)


class EmailCodeEndToEndTests(TestCase):
    def test_full_flow_registration_verification_email_code(self):
        pw = h.random_password()
        h.register(self.client, "e2e@example.org", pw)
        h.verify_from_mail(self.client)

        r = h.login_password(self.client, "e2e@example.org", pw)
        self.assertRedirects(r, reverse("two_factor:setup"), fetch_redirect_response=False)
        # Passwort allein: keine Mitgliedsrechte, kein geschuetzter Bereich, kein Admin.
        self.assertEqual(self.client.get(reverse("account:home")).context["state"], MFA_PENDING)
        self.assertNotEqual(self.client.get(reverse(PROTECTED)).status_code, 200)

        r = h.setup_email(self.client)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(h.is_fully_verified(self.client))
        self.assertEqual(self.client.get(reverse(PROTECTED)).status_code, 200)

        self.client.post(reverse("account:logout"))
        # 60-s-Cooldown seit dem Setup-Code als abgelaufen simulieren.
        EmailDevice.objects.filter(user__email="e2e@example.org").update(last_generated_timestamp=None)
        r = h.login_password(self.client, "e2e@example.org", pw)
        self.assertEqual(r.status_code, 200)  # Token-Schritt
        self.assertNotIn("_auth_user_id", self.client.session)
        code = h.last_code()
        self.assertNotIn(code, mail.outbox[-1].subject)
        r = h.login_token(self.client, code)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(h.is_fully_verified(self.client))


class TotpEndToEndTests(TestCase):
    def test_full_flow_with_totp_setup_and_relogin(self):
        pw = h.random_password()
        h.register(self.client, "totp@example.org", pw)
        h.verify_from_mail(self.client)
        h.login_password(self.client, "totp@example.org", pw)
        r, key = h.setup_totp(self.client)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(h.is_fully_verified(self.client))
        user = User.objects.get(email="totp@example.org")
        self.assertFalse(EmailDevice.objects.filter(user=user).exists())  # kein E-Mail-Fallback

        self.client.post(reverse("account:logout"))
        mails_before = len(mail.outbox)
        h.login_password(self.client, "totp@example.org", pw)
        self.assertEqual(len(mail.outbox), mails_before)  # TOTP-only: keine E-Mail
        r = h.login_token(self.client, h.totp_code(key, 1))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(h.is_fully_verified(self.client))

    def test_totp_replay_rejected(self):
        pw = h.random_password()
        user = h.make_active_user("replay@example.org", pw)
        h.login_password(self.client, user.email, pw)
        _, key = h.setup_totp(self.client)
        self.client.post(reverse("account:logout"))
        code = h.totp_code(key, 1)
        h.login_password(self.client, user.email, pw)
        self.assertEqual(h.login_token(self.client, code).status_code, 302)
        self.client.post(reverse("account:logout"))
        h.login_password(self.client, user.email, pw)
        r = h.login_token(self.client, code)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(h.is_fully_verified(self.client))


class EmailCodeRulesTests(TestCase):
    def setUp(self):
        self.pw = h.random_password()
        self.user = h.make_active_user("codes@example.org", self.pw)
        EmailDevice.objects.create(user=self.user, name="default", confirmed=True)

    def _start(self, client):
        return h.login_password(client, self.user.email, self.pw)

    def test_code_cannot_be_reused(self):
        self._start(self.client)
        code = h.last_code()
        self.assertEqual(h.login_token(self.client, code).status_code, 302)
        other = Client()
        self._start(other)  # Cooldown: kein neuer Code, alte Challenge gehoert anderer Sitzung
        r = h.login_token(other, code)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("_auth_user_id", other.session)

    def test_five_failures_lock_challenge_even_for_correct_code(self):
        self._start(self.client)
        code = h.last_code()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            h.login_token(self.client, wrong)
        r = h.login_token(self.client, code)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_resend_respects_cooldown(self):
        self._start(self.client)
        self.assertEqual(len(mail.outbox), 1)
        device = EmailDevice.objects.get(user=self.user)
        r = self.client.post(reverse("two_factor:login"), {
            f"{h.LOGIN}-current_step": "token", "challenge_device": device.persistent_id,
        })
        self.assertContains(r, "erst nach einer Minute")
        self.assertEqual(len(mail.outbox), 1)

    def test_new_challenge_invalidates_previous_code_and_parallel_flow(self):
        client_a, client_b = Client(), Client()
        self._start(client_a)
        code_a = h.last_code()
        # 60 s spaeter: Geraete-Cooldown und Zweck-Mindestabstand abgelaufen.
        EmailDevice.objects.filter(user=self.user).update(last_generated_timestamp=None)
        rate_limits.RateEvent.objects.filter(bucket__scope="mail_purpose_recipient").delete()
        self._start(client_b)
        code_b = h.last_code()
        # A: alter Code ungueltig, Code von B gehoert zu fremder Challenge.
        self.assertEqual(h.login_token(client_a, code_a).status_code, 200)
        self.assertEqual(h.login_token(client_a, code_b).status_code, 200)
        self.assertNotIn("_auth_user_id", client_a.session)
        self.assertEqual(LoginChallenge.objects.filter(user=self.user, state="active").count(), 1)
        self.assertEqual(h.login_token(client_b, code_b).status_code, 302)

    def test_smtp_failure_gives_no_password_only_fallback(self):
        with mock.patch("django.core.mail.backends.locmem.EmailBackend.send_messages", side_effect=OSError("down")):
            r = self._start(self.client)
        self.assertContains(r, "konnte derzeit nicht gesendet werden")
        self.assertNotIn("_auth_user_id", self.client.session)
        r = h.login_token(self.client, "123456")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_mail_budget_per_recipient(self):
        for _ in range(8):
            EmailDevice.objects.filter(user=self.user).update(last_generated_timestamp=None)
            rate_limits.RateEvent.objects.filter(bucket__scope="mail_purpose_recipient").delete()
            self._start(Client())
        self.assertEqual(len(mail.outbox), 5)  # 5/Stunde je Empfaenger

    def test_unverified_device_address_gets_no_code(self):
        EmailDevice.objects.filter(user=self.user).update(email="fremd@example.org")
        self._start(self.client)
        self.assertEqual(len(mail.outbox), 0)
        self.assertNotIn("_auth_user_id", self.client.session)


class SessionSecurityTests(TestCase):
    def setUp(self):
        self.pw = h.random_password()
        self.user = h.make_active_user("s@example.org", self.pw)
        h.login_password(self.client, self.user.email, self.pw)
        _, self.key = h.setup_totp(self.client)

    def test_session_key_rotates_on_login(self):
        self.client.post(reverse("account:logout"))
        h.login_password(self.client, self.user.email, self.pw)
        before = self.client.session.session_key
        h.login_token(self.client, h.totp_code(self.key, 1))
        self.assertNotEqual(before, self.client.session.session_key)

    def test_suspension_ends_existing_session(self):
        User.objects.filter(pk=self.user.pk).update(state=User.State.SUSPENDED)
        self.client.get("/")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_generation_change_ends_session(self):
        User.objects.filter(pk=self.user.pk).update(security_generation=99)
        self.assertEqual(self.client.get(reverse(PROTECTED)).status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_idle_timeout(self):
        session = self.client.session
        session["acct_last_seen"] -= 61 * 60
        session.save()
        self.client.get("/")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_requires_post_and_clears_cache(self):
        self.assertEqual(self.client.get(reverse("account:logout")).status_code, 405)
        r = self.client.post(reverse("account:logout"))
        self.assertEqual(r["Clear-Site-Data"], '"cache"')
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_open_redirect_blocked(self):
        self.client.post(reverse("account:logout"))
        h.login_password(self.client, self.user.email, self.pw, next_url="https://evil.example/")
        r = h.login_token(self.client, h.totp_code(self.key, 1), next_url="https://evil.example/")
        self.assertEqual(r.status_code, 302)
        self.assertFalse(r["Location"].startswith("https://evil.example"))

    def test_disable_and_profile_routes_do_not_exist(self):
        for path in ("/account/two_factor/disable/", "/konto/two_factor/disable/", "/account/two_factor/"):
            self.assertEqual(self.client.get(path).status_code, 404)


class AdminAccessTests(TestCase):
    def _staff(self, email, **kw):
        pw = h.random_password()
        user = h.make_active_user(email, pw)
        user.is_staff = True
        for k, v in kw.items():
            setattr(user, k, v)
        user.save()
        return user, pw

    def test_password_only_staff_has_no_admin(self):
        user, pw = self._staff("staff1@example.org")
        h.login_password(self.client, user.email, pw)
        r = self.client.get("/verwaltung/", follow=True)
        self.assertEqual(r.redirect_chain[-1][0].split("?")[0], reverse("two_factor:login"))
        self.assertNotContains(r, "Adresskarte – Verwaltung")

    def test_fully_verified_staff_reaches_admin(self):
        user, pw = self._staff("staff2@example.org")
        h.login_password(self.client, user.email, pw)
        h.setup_totp(self.client)
        self.assertEqual(self.client.get("/verwaltung/").status_code, 200)

    def test_superuser_needs_second_step_too(self):
        user, pw = self._staff("root@example.org", is_superuser=True)
        h.login_password(self.client, user.email, pw)
        self.assertNotEqual(self.client.get("/verwaltung/").status_code, 200)
        self.assertNotEqual(self.client.get(reverse(PROTECTED)).status_code, 200)


class PasswordResetTests(TestCase):
    def setUp(self):
        self.pw = h.random_password()
        self.user = h.make_active_user("reset@example.org", self.pw)
        h.login_password(self.client, self.user.email, self.pw)
        _, self.key = h.setup_totp(self.client)

    def _reset(self, email="reset@example.org"):
        anon = Client()
        anon.post(reverse("account:password_reset"), {"email": email})
        return anon

    def test_reset_does_not_log_in_and_keeps_totp(self):
        anon = self._reset()
        link = h.last_link()
        path = "/" + link.split("://", 1)[1].split("/", 1)[1]
        r = anon.get(path, follow=True)
        new_pw = h.random_password()
        form_url = r.redirect_chain[-1][0]
        anon.post(form_url, {"new_password1": new_pw, "new_password2": new_pw})
        self.assertNotIn("_auth_user_id", anon.session)
        # Bestehende Sitzung beendet.
        self.client.get("/")
        self.assertNotIn("_auth_user_id", self.client.session)
        # Neues Passwort + weiterhin TOTP, kein E-Mail-Geraet entstanden.
        h.login_password(anon, self.user.email, new_pw)
        self.assertNotIn("_auth_user_id", anon.session)
        self.assertFalse(EmailDevice.objects.filter(user=self.user).exists())
        self.assertEqual(h.login_token(anon, h.totp_code(self.key, 1)).status_code, 302)

    def test_no_mail_for_unknown_pending_or_unusable(self):
        User.objects.create_user("pending@example.org", h.random_password())  # pending_email
        h.make_active_user("nopw@example.org", None)
        mail.outbox.clear()
        for email in ("unknown@example.org", "pending@example.org", "nopw@example.org"):
            self._reset(email)
        self.assertEqual(len(mail.outbox), 0)


class AxesTests(TestCase):
    def test_password_lockout_after_failures(self):
        pw = h.random_password()
        user = h.make_active_user("axes@example.org", pw)
        for _ in range(5):
            h.login_password(self.client, user.email, "falsch-" + pw)
        r = h.login_password(self.client, user.email, pw)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertNotEqual(r.status_code, 302)


class RateLimiterTests(TestCase):
    def test_all_or_nothing_and_rolling_window(self):
        a = rate_limits.Limit("t_a", "k", 2, 3600)
        b = rate_limits.Limit("t_b", "k", 1, 3600)
        rate_limits.reserve([a, b])
        with self.assertRaises(rate_limits.RateLimited):
            rate_limits.reserve([a, b])
        # a darf noch einmal, weil der abgelehnte Versuch nichts gebucht hat
        rate_limits.reserve([a])
        with self.assertRaises(rate_limits.RateLimited) as ctx:
            rate_limits.reserve([a])
        self.assertGreater(ctx.exception.retry_after, 3500)

    def test_keys_are_pseudonymized(self):
        rate_limits.reserve([rate_limits.Limit("t_ip", "203.0.113.7", 5, 60)])
        self.assertFalse(rate_limits.RateBucket.objects.filter(key__contains="203.0.113").exists())


class RateLimiterConcurrencyTests(TransactionTestCase):
    def test_parallel_reservations_never_exceed_limit(self):
        limit = rate_limits.Limit("t_conc", "k", 5, 3600)
        ok, barrier = [], threading.Barrier(12)

        def worker():
            try:
                barrier.wait()
                rate_limits.reserve([limit])
                ok.append(1)
            except rate_limits.RateLimited:
                pass
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(ok), 5)


class TotpConcurrencyTests(TransactionTestCase):
    def test_same_totp_code_accepted_only_once_in_parallel(self):
        pw = h.random_password()
        user = h.make_active_user("conc@example.org", pw)
        device = TOTPDevice.objects.create(user=user, name="default", confirmed=True)
        clients = [Client(), Client(), Client()]
        for c in clients:
            h.login_password(c, user.email, pw)
        code = h.totp_code(device.bin_key, 1)
        results, barrier = [], threading.Barrier(len(clients))

        def worker(c):
            try:
                barrier.wait()
                results.append(h.login_token(c, code).status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(c,)) for c in clients]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), [200, 200, 302])


class CleanupTests(TestCase):
    def test_cleanup_removes_only_expired_state(self):
        from datetime import timedelta
        from io import StringIO

        from django.core.management import call_command
        from django.utils import timezone

        old = User.objects.create_user("alt@example.org", h.random_password())
        User.objects.filter(pk=old.pk).update(date_joined=timezone.now() - timedelta(days=8))
        fresh = User.objects.create_user("frisch@example.org", h.random_password())
        active = h.make_active_user("aktiv@example.org", h.random_password())
        User.objects.filter(pk=active.pk).update(date_joined=timezone.now() - timedelta(days=30))
        rate_limits.reserve([rate_limits.Limit("t_clean", "k", 5, 60)])
        rate_limits.RateEvent.objects.update(created_at=timezone.now() - timedelta(hours=49))
        out = StringIO()
        call_command("cleanup_security_state", stdout=out)
        self.assertFalse(User.objects.filter(pk=old.pk).exists())
        self.assertTrue(User.objects.filter(pk__in=[fresh.pk, active.pk]).count() == 2)
        self.assertEqual(rate_limits.RateEvent.objects.count(), 0)

    def test_method_choices_are_restricted_and_labelled(self):
        pw = h.random_password()
        user = h.make_active_user("m@example.org", pw)
        h.login_password(self.client, user.email, pw)
        self.client.get(reverse("two_factor:setup"))
        r = h.setup_post(self.client, "welcome")
        choices = dict(r.context["wizard"]["form"].fields["method"].choices)
        self.assertEqual(set(choices), {"generator", "email"})
        self.assertIn("Authenticator-App", choices["generator"])
