"""Etappe 4: Betreuerportal, Redaktion, Administration, frische Bestaetigung, E-Mail-Aenderung."""

import re
import threading
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django_otp.plugins.otp_email.models import EmailDevice
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

from locations.models import AuditEvent, Location, Photo, Provider, PublishedLocation
from locations.roles import EDITOR, OBJECT_MANAGER, ensure_role_groups
from locations.services import publishing

from . import helpers_auth as h
from .test_stage3_member import image_bytes, make_published

User = get_user_model()


def verified(email, *, groups=(), staff=False, superuser=False):
    """Echter Login mit TOTP-Einrichtung; gibt (client, user) zurueck."""
    ensure_role_groups()
    pw = h.random_password()
    user = h.make_active_user(email, pw)
    user.is_staff, user.is_superuser = staff or superuser, superuser
    user.save()
    user.groups.set(Group.objects.filter(name__in=groups))
    client = Client()
    h.login_password(client, email, pw)
    h.setup_totp(client)
    return client, user


def backup_code(user) -> str:
    device, _ = StaticDevice.objects.get_or_create(user=user, name="backup")
    token = StaticToken.random_token()
    device.token_set.create(token=token)
    return token


def set_known_password(client, user) -> str:
    """Setzt ein bekanntes Passwort nur im Passwortfeld (kein Ueberschreiben anderer Felder)
    und haelt die Testsitzung gueltig."""
    from django.contrib.auth.hashers import make_password

    pw = h.random_password()
    User.objects.filter(pk=user.pk).update(password=make_password(pw))
    user.refresh_from_db()
    session = client.session
    session["_auth_user_hash"] = user.get_session_auth_hash()
    session.save()
    return pw


def reauth(client, user):
    pw = set_known_password(client, user)
    return client.post(reverse("account:reauth"), {"password": pw, "otp_token": backup_code(user), "next": "/konto/"})


def owner_location(owner, **kw):
    data = dict(title="Eigenes Objekt", categories=["beratung"], preview_text="Vorschau", long_description="Lang",
                place_text="Bern", exact_lat=Decimal("46.95"), exact_lon=Decimal("7.44"),
                editorial_notes="CANARY-REDAKTION", managed_by=owner)
    data.update(kw)
    return Location.objects.create(**data)


def edit_post(loc, **overrides):
    data = {
        "expected_version": loc.work_version, "title": loc.title, "categories": loc.categories,
        "preview_text": loc.preview_text, "long_description": loc.long_description, "details": "",
        "place_text": loc.place_text, "exact_address": "", "exact_lat": "46.95", "exact_lon": "7.44",
        "preview_lat": "", "preview_lon": "", "contact_name": "", "contact_organisation": "",
        "contact_email": "", "contact_phone": "", "contact_website": "", "provider": "", "internal_notes": "",
    }
    data.update(overrides)
    return data


class PortalAccessTests(TestCase):
    def setUp(self):
        self.a, self.user_a = verified("a@example.org", groups=[OBJECT_MANAGER])
        self.b, self.user_b = verified("b@example.org", groups=[OBJECT_MANAGER])
        self.loc_a = owner_location(self.user_a, title="Objekt A")
        self.loc_b = owner_location(self.user_b, title="Objekt B")

    def test_visitor_without_role_has_no_portal(self):
        visitor, _ = verified("v@example.org")
        self.assertEqual(visitor.get(reverse("portal:list")).status_code, 404)

    def test_create_assigns_current_user_ignoring_post_fields(self):
        r = self.a.post(reverse("portal:create"), {"title": "Neu", "categories": ["bildung"],
                                                    "managed_by": self.user_b.pk, "review_state": "approved"})
        loc = Location.objects.get(title="Neu")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(loc.managed_by, self.user_a)
        self.assertEqual(loc.review_state, "draft")
        self.assertFalse(PublishedLocation.objects.filter(location=loc).exists())

    def test_list_shows_only_own_objects_and_hides_editorial_notes(self):
        body = self.a.get(reverse("portal:list")).content.decode()
        self.assertIn("Objekt A", body)
        self.assertNotIn("Objekt B", body)
        self.assertNotContains(self.a.get(reverse("portal:edit", args=[self.loc_a.public_id])), "CANARY-REDAKTION")

    def test_foreign_object_routes_are_404(self):
        pid = self.loc_b.public_id
        processed_photo = Photo.objects.create(location=self.loc_b, storage_key="0" * 32)
        self.assertEqual(self.a.get(reverse("portal:edit", args=[pid])).status_code, 404)
        self.assertEqual(self.a.get(reverse("portal:preview", args=[pid])).status_code, 404)
        for name in ("submit", "withdraw", "withdraw_contacts"):
            self.assertEqual(self.a.post(reverse(f"portal:{name}", args=[pid]), {"expected_version": 1}).status_code, 404)
        self.assertEqual(self.a.post(reverse("portal:edit", args=[pid]), edit_post(self.loc_b, title="Gekapert")).status_code, 404)
        upload = SimpleUploadedFile("x.jpg", image_bytes(), content_type="image/jpeg")
        self.assertEqual(self.a.post(reverse("portal:photo_upload", args=[pid]),
                                     {"image": upload, "visibility": "internal", "rights_confirmed": "on"}).status_code, 404)
        # Fremdes Foto unter eigenem Objekt referenzieren
        r = self.a.post(reverse("portal:photo_update", args=[self.loc_a.public_id, processed_photo.public_id]),
                        {"action": "withdraw"})
        self.assertEqual(r.status_code, 404)
        self.loc_b.refresh_from_db()
        self.assertEqual(self.loc_b.title, "Objekt B")
        self.assertFalse(Photo.objects.get(pk=processed_photo.pk).is_withdrawn)

    def test_hidden_fields_cannot_be_changed(self):
        r = self.a.post(reverse("portal:edit", args=[self.loc_a.public_id]), edit_post(
            self.loc_a, managed_by=self.user_b.pk, review_state="approved", editorial_notes="x",
            work_version=99, is_demo="on", public_id="abc", review_feedback="x"))
        self.assertEqual(r.status_code, 302)
        self.loc_a.refresh_from_db()
        self.assertEqual(self.loc_a.managed_by, self.user_a)
        self.assertEqual(self.loc_a.review_state, "draft")
        self.assertEqual(self.loc_a.editorial_notes, "CANARY-REDAKTION")
        self.assertEqual(self.loc_a.work_version, 2)
        self.assertFalse(self.loc_a.is_demo)

    def test_foreign_provider_rejected(self):
        foreign = Provider.objects.create(name="Fremd", managed_by=self.user_b)
        r = self.a.post(reverse("portal:edit", args=[self.loc_a.public_id]), edit_post(self.loc_a, provider=foreign.pk))
        self.assertEqual(r.status_code, 200)  # Formularfehler
        self.loc_a.refresh_from_db()
        self.assertIsNone(self.loc_a.provider)

    def test_invalid_coordinates_rejected(self):
        for lat, lon in (("NaN", "7.4"), ("inf", "7.4"), ("55", "7.4"), ("46.9", "")):
            with self.subTest(lat=lat, lon=lon):
                r = self.a.post(reverse("portal:edit", args=[self.loc_a.public_id]),
                                edit_post(self.loc_a, exact_lat=lat, exact_lon=lon))
                self.assertEqual(r.status_code, 200)
        self.loc_a.refresh_from_db()
        self.assertEqual(self.loc_a.work_version, 1)

    def test_version_conflict_from_stale_form(self):
        stale = edit_post(self.loc_a, title="Erste Aenderung")
        self.a.post(reverse("portal:edit", args=[self.loc_a.public_id]), stale)
        r = self.a.post(reverse("portal:edit", args=[self.loc_a.public_id]), {**stale, "title": "Veraltet"}, follow=True)
        self.assertContains(r, "inzwischen geaendert")
        self.loc_a.refresh_from_db()
        self.assertEqual(self.loc_a.title, "Erste Aenderung")

    def test_role_revocation_and_reassignment_take_effect_immediately(self):
        url = reverse("portal:edit", args=[self.loc_a.public_id])
        self.assertEqual(self.a.get(url).status_code, 200)
        Location.objects.filter(pk=self.loc_a.pk).update(managed_by=self.user_b)
        self.assertEqual(self.a.post(url, edit_post(self.loc_a, title="Nach Entzug")).status_code, 404)
        self.assertEqual(self.b.get(url).status_code, 200)
        self.user_b.groups.clear()
        self.assertEqual(self.b.get(url).status_code, 404)

    @override_settings(MAX_ACTIVE_PHOTOS_PER_LOCATION=1)
    def test_photo_upload_rules(self):
        url = reverse("portal:photo_upload", args=[self.loc_a.public_id])
        bad = SimpleUploadedFile("x.jpg", b"<svg></svg>", content_type="image/jpeg")
        r = self.a.post(url, {"image": bad, "visibility": "guest_preview", "rights_confirmed": "on"}, follow=True)
        self.assertContains(r, "Bild abgelehnt")
        good = SimpleUploadedFile("x.jpg", image_bytes(), content_type="image/jpeg")
        self.a.post(url, {"image": good, "visibility": "member_detail", "rights_confirmed": "on", "alt_text": "Eingang"})
        photo = Photo.objects.get(location=self.loc_a)
        self.assertEqual(photo.uploaded_by, self.user_a)
        self.assertEqual(self.a.get(reverse("locations:photo", args=[photo.public_id, "vorschau"])).status_code, 200)
        self.assertEqual(self.b.get(reverse("locations:photo", args=[photo.public_id, "vorschau"])).status_code, 404)
        third = SimpleUploadedFile("y.jpg", image_bytes(), content_type="image/jpeg")
        r = self.a.post(url, {"image": third, "visibility": "internal", "rights_confirmed": "on"}, follow=True)
        self.assertContains(r, "Maximale Anzahl")
        self.assertEqual(Photo.objects.filter(location=self.loc_a).count(), 1)


class WorkflowTests(TestCase):
    def setUp(self):
        self.owner_client, self.owner = verified("o@example.org", groups=[OBJECT_MANAGER])
        self.editor_client, self.editor = verified("r@example.org", groups=[EDITOR], staff=True)
        self.loc, self.pub, _ = make_published(1, photos=False)
        Location.objects.filter(pk=self.loc.pk).update(managed_by=self.owner)
        self.loc.refresh_from_db()

    def _decide(self, action, version, **extra):
        return self.editor_client.post(reverse("admin:locations_location_decide", args=[self.loc.pk]),
                                       {"action": action, "expected_version": version, **extra})

    def _public(self):
        guest = Client()
        return guest.get(reverse("locations:preview", args=[self.pub.public_id]))

    def test_edit_after_publication_stays_private_until_review(self):
        self.owner_client.post(reverse("portal:edit", args=[self.loc.public_id]),
                               edit_post(self.loc, title="Neuer Titel UNGEPRUEFT"))
        self.assertNotContains(self._public(), "UNGEPRUEFT")
        self.loc.refresh_from_db()
        self.owner_client.post(reverse("portal:submit", args=[self.loc.public_id]),
                               {"expected_version": self.loc.work_version})
        self.assertNotContains(self._public(), "UNGEPRUEFT")
        self._decide("publish", self.loc.work_version)
        self.assertContains(self._public(), "UNGEPRUEFT")

    def test_stale_publish_is_rejected(self):
        self.owner_client.post(reverse("portal:submit", args=[self.loc.public_id]),
                               {"expected_version": self.loc.work_version})
        old_version = self.loc.work_version
        self.owner_client.post(reverse("portal:edit", args=[self.loc.public_id]),
                               edit_post(self.loc, title="Nach Pruefung geaendert"))
        r = self._decide("publish", old_version)
        self.assertEqual(r.status_code, 302)
        self.assertNotContains(self._public(), "Nach Pruefung geaendert")

    def test_reject_feedback_visible_to_owner_editorial_notes_not(self):
        Location.objects.filter(pk=self.loc.pk).update(editorial_notes="CANARY-REDAKTION")
        self.owner_client.post(reverse("portal:submit", args=[self.loc.public_id]),
                               {"expected_version": self.loc.work_version})
        self._decide("reject", self.loc.work_version, feedback="Bitte Kontakt ergaenzen")
        page = self.owner_client.get(reverse("portal:edit", args=[self.loc.public_id]))
        self.assertContains(page, "Bitte Kontakt ergaenzen")
        self.assertNotContains(page, "CANARY-REDAKTION")

    def test_owner_withdraws_immediately_but_cannot_republish(self):
        self.owner_client.post(reverse("portal:withdraw", args=[self.loc.public_id]))
        self.assertEqual(self._public().status_code, 404)
        r = self.owner_client.post(reverse("admin:locations_location_decide", args=[self.loc.pk]),
                                   {"action": "publish", "expected_version": self.loc.work_version})
        self.assertNotEqual(r.status_code, 200)
        self.assertFalse(PublishedLocation.objects.get(pk=self.pub.pk).is_active)

    def test_contact_withdrawal_stops_member_delivery(self):
        member, _ = verified("m@example.org")
        member.post(reverse("locations:open_details", args=[self.pub.public_id]))
        self.assertContains(member.get(reverse("locations:preview", args=[self.pub.public_id])), "kontakt1@example.org")
        self.owner_client.post(reverse("portal:withdraw_contacts", args=[self.loc.public_id]))
        self.assertNotContains(member.get(reverse("locations:preview", args=[self.pub.public_id])), "kontakt1@example.org")

    def test_editor_admin_scope(self):
        c = self.editor_client
        self.assertEqual(c.get(reverse("admin:locations_location_changelist")).status_code, 200)
        self.assertEqual(c.get(reverse("admin:locations_location_compare", args=[self.loc.pk])).status_code, 200)
        self.assertEqual(c.get(reverse("admin:locations_user_changelist")).status_code, 403)
        self.assertEqual(c.get(f"/verwaltung/otp_totp/totpdevice/").status_code, 404)
        self.assertEqual(c.get(f"/verwaltung/auth/group/").status_code, 404)
        changelist = c.get(reverse("admin:locations_location_changelist")).content.decode()
        self.assertNotIn("delete_selected", changelist)
        self.assertNotIn('name="action"', changelist)

    def test_assignment_only_to_existing_object_managers(self):
        visitor = h.make_active_user("nobody@example.org", h.random_password())
        url = reverse("admin:locations_location_change", args=[self.loc.pk])
        form = self.editor_client.get(url).context["adminform"].form
        self.assertNotIn(visitor, form.fields["managed_by"].queryset)
        self.assertIn(self.owner, form.fields["managed_by"].queryset)
        self.assertFalse(self.owner.is_staff)

    def test_visitor_and_owner_cannot_use_admin(self):
        self.assertNotEqual(self.owner_client.get(reverse("admin:locations_location_changelist")).status_code, 200)
        r = self.owner_client.get(reverse("admin:locations_location_compare", args=[self.loc.pk]))
        self.assertNotEqual(r.status_code, 200)


class SaveVsPublishRaceTests(TransactionTestCase):
    def test_concurrent_owner_save_and_publish_never_publish_unreviewed(self):
        loc, pub, _ = make_published(1, photos=False)
        publishing.submit(loc.pk, None, loc.work_version)
        reviewed_version = Location.objects.get(pk=loc.pk).work_version
        barrier = threading.Barrier(2)
        errors = []

        def owner_save():
            try:
                barrier.wait()
                publishing.save_work_version(loc.pk, None, reviewed_version, {"title": "UNGEPRUEFT"})
            except publishing.VersionConflict:
                errors.append("save")
            finally:
                connection.close()

        def editor_publish():
            try:
                barrier.wait()
                publishing.publish(loc.pk, None, reviewed_version)
            except (publishing.VersionConflict, publishing.PublishError):
                errors.append("publish")
            finally:
                connection.close()

        threads = [threading.Thread(target=owner_save), threading.Thread(target=editor_publish)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertNotEqual(PublishedLocation.objects.get(pk=pub.pk).title, "UNGEPRUEFT")


class AdministrationTests(TestCase):
    def setUp(self):
        self.admin, self.admin_user = verified("admin@example.org", superuser=True)
        self.target_client, self.target = verified("ziel@example.org")

    def _change(self, **data):
        url = reverse("admin:locations_user_change", args=[self.target.pk])
        return self.admin.post(url, {"state": self.target.state, "roles": [], **data})

    def test_role_change_requires_reauth_then_is_logged_and_ends_sessions(self):
        r = self._change(roles=[OBJECT_MANAGER])
        self.assertIn(reverse("account:reauth"), r["Location"])
        self.assertFalse(self.target.groups.exists())
        self.assertEqual(reauth(self.admin, self.admin_user).status_code, 302)
        r = self._change(roles=[OBJECT_MANAGER])
        self.assertEqual(r.status_code, 302)
        self.assertTrue(self.target.groups.filter(name=OBJECT_MANAGER).exists())
        self.assertTrue(AuditEvent.objects.filter(action="role_grant", target_id=str(self.target.pk)).exists())
        self.target_client.get("/")
        self.assertNotIn("_auth_user_id", self.target_client.session)

    def test_suspension_ends_target_session(self):
        reauth(self.admin, self.admin_user)
        self._change(state="suspended")
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)
        r = self.target_client.get(reverse("account:home"))
        self.assertEqual(r.status_code, 302)  # Sitzung gibt keinen Zugang mehr
        self.assertIn(reverse("two_factor:login"), r["Location"])

    def test_no_secrets_on_user_page_and_no_self_demotion(self):
        page = self.admin.get(reverse("admin:locations_user_change", args=[self.target.pk])).content.decode()
        self.assertNotIn("pbkdf2", page)
        self.assertNotIn("md5$", page)
        from django_otp.plugins.otp_totp.models import TOTPDevice

        key = TOTPDevice.objects.get(user=self.target).key
        self.assertNotIn(key, page)
        reauth(self.admin, self.admin_user)
        self.admin.post(reverse("admin:locations_user_change", args=[self.admin_user.pk]),
                        {"state": "active", "roles": []})
        self.admin_user.refresh_from_db()
        self.assertTrue(self.admin_user.is_superuser)

    def test_editor_cannot_grant_roles(self):
        editor, _ = verified("ed@example.org", groups=[EDITOR], staff=True)
        r = editor.post(reverse("admin:locations_user_change", args=[self.target.pk]),
                        {"state": "active", "roles": ["admin"]})
        self.assertIn(r.status_code, (302, 403))
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_superuser)


class ReauthAndBackupTests(TestCase):
    def setUp(self):
        self.client_, self.user = verified("re@example.org")

    def test_backup_code_generation_requires_fresh_confirmation(self):
        url = reverse("two_factor:backup_tokens")
        r = self.client_.post(url)
        self.assertIn(reverse("account:reauth"), r["Location"])
        self.assertFalse(StaticToken.objects.filter(device__user=self.user).exists())
        reauth(self.client_, self.user)
        StaticToken.objects.filter(device__user=self.user).delete()
        self.client_.post(url)
        self.assertEqual(StaticToken.objects.filter(device__user=self.user).count(), 10)

    def test_reauth_expires(self):
        reauth(self.client_, self.user)
        session = self.client_.session
        session["reauth_at"] -= 6 * 60
        session.save()
        r = self.client_.post(reverse("two_factor:backup_tokens"))
        self.assertIn(reverse("account:reauth"), r["Location"])

    def test_wrong_code_or_password_fails(self):
        pw = set_known_password(self.client_, self.user)
        r = self.client_.post(reverse("account:reauth"), {"password": pw, "otp_token": "000000"})
        self.assertEqual(r.status_code, 200)
        r = self.client_.post(reverse("account:reauth"), {"password": "falsch", "otp_token": backup_code(self.user)})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("reauth_at", self.client_.session)


class EmailChangeTests(TestCase):
    def setUp(self):
        self.pw = h.random_password()
        self.user = h.make_active_user("alt@example.org", self.pw)
        EmailDevice.objects.create(user=self.user, name="default", confirmed=True)
        self.client_ = Client()
        h.login_password(self.client_, self.user.email, self.pw)
        h.login_token(self.client_, h.last_code())

    def _reauth_with_email_code(self):
        EmailDevice.objects.filter(user=self.user).update(last_generated_timestamp=None)
        self.client_.post(reverse("account:reauth"), {"send_code": "1", "next": "/konto/"})
        return self.client_.post(reverse("account:reauth"), {"password": self.pw, "otp_token": h.last_code(), "next": "/konto/"})

    def test_change_requires_reauth_and_confirmation(self):
        self.assertIn(reverse("account:reauth"), self.client_.get(reverse("account:email_change"))["Location"])
        self.assertEqual(self._reauth_with_email_code().status_code, 302)
        mail.outbox.clear()
        self.client_.post(reverse("account:email_change"), {"new_email": "neu@example.org"})
        recipients = sorted(m.to[0] for m in mail.outbox)
        self.assertEqual(recipients, ["alt@example.org", "neu@example.org"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "alt@example.org")  # bis zur Bestaetigung unveraendert
        link = re.search(r"https?://\S+", [m for m in mail.outbox if m.to == ["neu@example.org"]][0].body).group(0)
        path = "/" + link.split("://", 1)[1].split("/", 1)[1]
        anon = Client()
        anon.get(path)
        r = anon.post(reverse("account:email_change_confirm"))
        self.assertContains(r, "E-Mail-Adresse geändert")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "neu@example.org")
        self.client_.get("/")
        self.assertNotIn("_auth_user_id", self.client_.session)  # neu anmelden
        # Replay scheitert
        anon2 = Client()
        anon2.get(path)
        self.assertEqual(anon2.post(reverse("account:email_change_confirm")).status_code, 400)
        # Login-Code geht jetzt an die neue Adresse
        EmailDevice.objects.filter(user=self.user).update(last_generated_timestamp=None)
        mail.outbox.clear()
        fresh = Client()
        h.login_password(fresh, "neu@example.org", self.pw)
        self.assertEqual(mail.outbox[-1].to, ["neu@example.org"])

    def test_taken_address_gets_no_confirmation(self):
        h.make_active_user("besetzt@example.org", h.random_password())
        self._reauth_with_email_code()
        mail.outbox.clear()
        self.client_.post(reverse("account:email_change"), {"new_email": "besetzt@example.org"})
        self.assertEqual([m.to[0] for m in mail.outbox], ["alt@example.org"])


class AdminFormVersionTests(TestCase):
    def test_editor_form_detects_concurrent_owner_change(self):
        from locations.admin import LocationAdminForm

        loc = owner_location(None)
        stale_form_version = loc.work_version
        publishing.save_work_version(loc.pk, None, loc.work_version, {"title": "Vom Betreuer geaendert"})
        form = LocationAdminForm(data={"title": "Redaktion", "categories": ["beratung"],
                                       "expected_version": stale_form_version}, instance=Location.objects.get(pk=loc.pk))
        self.assertFalse(form.is_valid())
        self.assertIn("inzwischen geaendert", str(form.non_field_errors()))
