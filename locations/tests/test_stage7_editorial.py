"""Redaktionelle Direktfreigabe, Pflicht-Betreuung, Mitteilungen und Fotoverwaltung durch die Redaktion."""

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from locations import rate_limits
from locations.models import Location, LocationNotice, Photo, PublishedLocation
from locations.roles import EDITOR, OBJECT_MANAGER
from locations.services import publishing

from .test_stage1_guest import make_location
from .test_stage3_member import image_bytes, make_published
from .test_stage4_work import edit_post, verified

DEFAULT_MANAGER = "standard-betreuung@example.org"


def admin_form_data(response, **overrides):
    """POST-Daten aus dem gerenderten Admin-Formular inkl. Inline-Formsets."""
    data = {}

    def put(form, name):
        value = form[name].value()
        key = form.add_prefix(name)
        if isinstance(value, bool):
            if value:
                data[key] = "on"
        elif isinstance(value, (list, tuple)):
            data[key] = [str(v) for v in value]
        else:
            data[key] = "" if value is None else str(value)

    form = response.context["adminform"].form
    for name in form.fields:
        put(form, name)
    for inline in response.context["inline_admin_formsets"]:
        fs = inline.formset
        for name in fs.management_form.fields:
            put(fs.management_form, name)
        for f in fs.forms:
            for name in f.fields:
                put(f, name)
    data.update(overrides)
    return data


class EditorialBase(TestCase):
    def setUp(self):
        self.owner_client, self.owner = verified("o@example.org", groups=[OBJECT_MANAGER])
        self.editor_client, self.editor = verified("r@example.org", groups=[EDITOR], staff=True)
        mail.outbox = []

    def decide(self, loc, action, **extra):
        loc.refresh_from_db()
        return self.editor_client.post(reverse("admin:locations_location_decide", args=[loc.pk]),
                                       {"action": action, "expected_version": loc.work_version, **extra})

    def admin_edit(self, loc, **overrides):
        url = reverse("admin:locations_location_change", args=[loc.pk])
        page = self.editor_client.get(url)
        self.assertEqual(page.status_code, 200)
        return self.editor_client.post(url, admin_form_data(page, **overrides))

    def published(self, managed_by=None):
        loc, pub, _ = make_published(1, photos=False)
        Location.objects.filter(pk=loc.pk).update(managed_by=managed_by or self.owner)
        loc.refresh_from_db()
        return loc, pub

    def guest_preview(self, pub):
        return Client().get(reverse("locations:preview", args=[pub.public_id]))


class DirectPublishRuleTests(EditorialBase):
    def test_new_object_needs_submission_by_owner(self):
        loc = make_location(managed_by=self.owner)
        page = self.editor_client.get(reverse("admin:locations_location_compare", args=[loc.pk]))
        self.assertContains(page, "erst nach Einreichung durch die Betreuung")
        self.decide(loc, "publish")
        self.assertFalse(PublishedLocation.objects.filter(location=loc).exists())
        publishing.submit(loc.pk, self.owner, loc.work_version)
        self.decide(loc, "publish")
        self.assertTrue(PublishedLocation.objects.get(location=loc).is_active)
        notice = LocationNotice.objects.get(location=loc)
        self.assertEqual(notice.kind, LocationNotice.Kind.PUBLISHED)
        self.assertEqual(notice.sections, "Erstveroeffentlichung")

    def test_object_without_manager_cannot_be_published(self):
        loc = make_location()
        publishing.submit(loc.pk, None, loc.work_version)
        r = self.decide(loc, "publish")
        self.assertEqual(r.status_code, 302)
        self.assertFalse(PublishedLocation.objects.filter(location=loc).exists())

    @override_settings(DEFAULT_OBJECT_MANAGER_EMAIL=DEFAULT_MANAGER)
    def test_default_manager_allows_first_direct_publication(self):
        _c, default = verified(DEFAULT_MANAGER, groups=[OBJECT_MANAGER])
        loc = make_location(managed_by=default)
        self.decide(loc, "publish")
        self.assertTrue(PublishedLocation.objects.get(location=loc).is_active)
        notice = LocationNotice.objects.get(location=loc)
        self.assertEqual(notice.kind, LocationNotice.Kind.DIRECT_PUBLISHED)
        self.assertEqual(notice.mail_status, LocationNotice.MailStatus.SENT)
        self.assertEqual(mail.outbox[-1].to, [DEFAULT_MANAGER])

    @override_settings(DEFAULT_OBJECT_MANAGER_EMAIL=DEFAULT_MANAGER)
    def test_default_manager_without_role_does_not_count(self):
        verified(DEFAULT_MANAGER)  # ohne Rolle Objektbetreuung
        loc = make_location(managed_by=self.owner)
        self.assertIn("Einreichung", publishing.editorial_publish_blocker(loc))

    def test_editor_correction_on_published_object_goes_live_with_mail(self):
        loc, pub = self.published()
        self.admin_edit(loc, title="Korrigierter Titel REDAKTION")
        loc.refresh_from_db()
        self.assertEqual(loc.title, "Korrigierter Titel REDAKTION")
        self.assertNotContains(self.guest_preview(pub), "REDAKTION")
        edit_notice = LocationNotice.objects.get(location=loc, kind=LocationNotice.Kind.EDITOR_EDIT)
        self.assertIn("Vorschau", edit_notice.sections)
        self.assertEqual(edit_notice.mail_status, LocationNotice.MailStatus.NONE)
        self.assertEqual(mail.outbox, [])

        self.decide(loc, "publish")
        self.assertContains(self.guest_preview(pub), "Korrigierter Titel REDAKTION")
        notice = LocationNotice.objects.get(location=loc, kind=LocationNotice.Kind.DIRECT_PUBLISHED)
        self.assertEqual(notice.sections, "Vorschau (Titel, Kategorie, Text, Ort)")
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["o@example.org"])
        self.assertIn("angepasst und freigegeben", message.body)
        self.assertIn("Vorschau (Titel, Kategorie, Text, Ort)", message.body)
        self.assertNotIn("http", message.body)
        self.assertNotIn("CANARY", message.body)
        self.assertNotIn("r@example.org", message.body)

    def test_owner_pending_changes_block_direct_publish_until_submitted(self):
        loc, pub = self.published()
        self.owner_client.post(reverse("portal:edit", args=[loc.public_id]),
                               edit_post(loc, title="Halbfertig BETREUUNG"))
        loc.refresh_from_db()
        self.assertTrue(loc.owner_changes_pending)
        page = self.editor_client.get(reverse("admin:locations_location_compare", args=[loc.pk]))
        self.assertContains(page, "unveroeffentlichte Aenderungen")
        self.decide(loc, "publish")
        self.assertNotContains(self.guest_preview(pub), "BETREUUNG")
        # Auch eine anschliessende Redaktionskorrektur hebt die Sperre nicht auf.
        self.admin_edit(loc, preview_text="Redaktion korrigiert")
        self.decide(loc, "publish")
        self.assertNotContains(self.guest_preview(pub), "BETREUUNG")
        loc.refresh_from_db()
        self.owner_client.post(reverse("portal:submit", args=[loc.public_id]),
                               {"expected_version": loc.work_version})
        self.decide(loc, "publish")
        self.assertContains(self.guest_preview(pub), "Halbfertig BETREUUNG")
        loc.refresh_from_db()
        self.assertFalse(loc.owner_changes_pending)

    def test_submitted_version_adjusted_by_editor_is_reported_as_adjusted(self):
        loc, pub = self.published()
        self.owner_client.post(reverse("portal:edit", args=[loc.public_id]), edit_post(loc, title="Von Betreuung"))
        loc.refresh_from_db()
        self.owner_client.post(reverse("portal:submit", args=[loc.public_id]),
                               {"expected_version": loc.work_version})
        self.admin_edit(loc, preview_text="Von Redaktion ergaenzt")
        self.decide(loc, "publish")
        self.assertContains(self.guest_preview(pub), "Von Redaktion ergaenzt")
        last = LocationNotice.objects.filter(location=loc).first()
        self.assertEqual(last.kind, LocationNotice.Kind.DIRECT_PUBLISHED)

    def test_withdrawn_object_needs_resubmission(self):
        loc, pub = self.published()
        publishing.withdraw(loc.pk, self.owner)
        self.admin_edit(loc, title="Wieder da?")
        self.decide(loc, "publish")
        pub.refresh_from_db()
        self.assertFalse(pub.is_active)

    def test_reject_and_withdraw_send_mail_and_portal_notice(self):
        loc, _pub = self.published()
        self.owner_client.post(reverse("portal:edit", args=[loc.public_id]), edit_post(loc, title="Neu"))
        loc.refresh_from_db()
        self.owner_client.post(reverse("portal:submit", args=[loc.public_id]),
                               {"expected_version": loc.work_version})
        self.decide(loc, "reject", feedback="Bitte Oeffnungszeiten ergaenzen")
        self.assertIn("Bitte Oeffnungszeiten ergaenzen", mail.outbox[-1].body)
        self.decide(loc, "withdraw")
        self.assertIn("zurueckgezogen", mail.outbox[-1].body)
        page = self.owner_client.get(reverse("portal:edit", args=[loc.public_id]))
        self.assertContains(page, "Mitteilungen der Redaktion")
        self.assertContains(page, "Bitte Oeffnungszeiten ergaenzen")
        self.assertNotContains(page, "r@example.org")

    def test_notice_mail_uses_own_budget_not_security_budget(self):
        loc, _pub = self.published()
        self.admin_edit(loc, title="Budgettest")
        self.decide(loc, "publish")
        buckets = set(rate_limits.RateEvent.objects.values_list("bucket__scope", flat=True))
        self.assertIn("notice_recipient", buckets)
        self.assertNotIn("mail_recipient", buckets)

    def test_portal_shows_only_notices_addressed_to_current_manager(self):
        loc, _pub = self.published()
        other_client, other = verified("o2@example.org", groups=[OBJECT_MANAGER])
        self.admin_edit(loc, title="Fuer alte Betreuung")
        self.decide(loc, "publish")
        self.admin_edit(loc, managed_by=str(other.pk))
        page = other_client.get(reverse("portal:edit", args=[loc.public_id]))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Mitteilungen der Redaktion")


class ManagerRequiredTests(EditorialBase):
    def test_admin_form_requires_manager(self):
        loc, _pub = self.published()
        r = self.admin_edit(loc, managed_by="")
        self.assertEqual(r.status_code, 200)  # Formular mit Fehler
        loc.refresh_from_db()
        self.assertEqual(loc.managed_by, self.owner)

    @override_settings(DEFAULT_OBJECT_MANAGER_EMAIL=DEFAULT_MANAGER)
    def test_new_object_defaults_to_default_manager(self):
        _c, default = verified(DEFAULT_MANAGER, groups=[OBJECT_MANAGER])
        page = self.editor_client.get(reverse("admin:locations_location_add"))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["adminform"].form["managed_by"].value(), default.pk)
        data = admin_form_data(page, title="Neu durch Redaktion", categories=["beratung"], preview_text="Kurz",
                               place_text="Bern", long_description="Lang", exact_lat="46.95", exact_lon="7.44")
        r = self.editor_client.post(reverse("admin:locations_location_add"), data)
        self.assertEqual(r.status_code, 302)
        loc = Location.objects.get(title="Neu durch Redaktion")
        self.assertEqual(loc.managed_by, default)
        self.decide(loc, "publish")
        self.assertTrue(PublishedLocation.objects.get(location=loc).is_active)


class EditorPhotoTests(EditorialBase):
    def photos_url(self, loc):
        return reverse("admin:locations_location_photos", args=[loc.pk])

    def test_editor_uploads_photo_visible_only_after_publication(self):
        loc, pub = self.published()
        version = loc.work_version
        upload = SimpleUploadedFile("x.jpg", image_bytes(), content_type="image/jpeg")
        r = self.editor_client.post(self.photos_url(loc), {"action": "upload", "image": upload,
                                                           "visibility": "guest_preview", "rights_confirmed": "on", "alt_text": "Eingang"})
        self.assertEqual(r.status_code, 302)
        photo = Photo.objects.get(location=loc)
        self.assertEqual(photo.uploaded_by, self.editor)
        loc.refresh_from_db()
        self.assertEqual(loc.work_version, version + 1)
        self.assertFalse(loc.owner_changes_pending)
        self.assertFalse(pub.photos.exists())
        self.decide(loc, "publish")
        pub.refresh_from_db()
        self.assertTrue(pub.photos.filter(photo=photo).exists())
        notice = LocationNotice.objects.get(location=loc, kind=LocationNotice.Kind.DIRECT_PUBLISHED)
        self.assertIn("Fotos", notice.sections)

    def test_rejected_upload_and_wrong_role(self):
        loc, _pub = self.published()
        bad = SimpleUploadedFile("x.jpg", b"<svg></svg>", content_type="image/jpeg")
        r = self.editor_client.post(self.photos_url(loc), {"action": "upload", "image": bad,
                                                           "visibility": "internal", "rights_confirmed": "on"}, follow=True)
        self.assertContains(r, "Bild abgelehnt")
        self.assertFalse(Photo.objects.filter(location=loc).exists())
        good = SimpleUploadedFile("x.jpg", image_bytes(), content_type="image/jpeg")
        r = self.owner_client.post(self.photos_url(loc), {"action": "upload", "image": good,
                                                          "visibility": "internal", "rights_confirmed": "on"})
        self.assertNotEqual(r.status_code, 200)
        self.assertFalse(Photo.objects.filter(location=loc).exists())

    def test_remove_unpublished_photo_deletes_it(self):
        loc, _pub = self.published()
        upload = SimpleUploadedFile("x.jpg", image_bytes(), content_type="image/jpeg")
        self.editor_client.post(self.photos_url(loc), {"action": "upload", "image": upload, "visibility": "internal", "rights_confirmed": "on"})
        photo = Photo.objects.get(location=loc)
        self.editor_client.post(self.photos_url(loc), {"action": "remove", "photo": photo.public_id})
        self.assertFalse(Photo.objects.filter(pk=photo.pk).exists())
        self.assertEqual(mail.outbox, [])

    def test_remove_published_photo_hides_immediately_and_informs_owner(self):
        loc, pub, created = make_published(2)
        Location.objects.filter(pk=loc.pk).update(managed_by=self.owner)
        guest_photo = created[Photo.Visibility.GUEST_PREVIEW]
        photo_url = reverse("locations:photo", args=[guest_photo.public_id, "vorschau"])
        guest = Client()
        self.assertEqual(guest.get(reverse("locations:preview", args=[pub.public_id])).status_code, 200)
        self.assertEqual(guest.get(photo_url).status_code, 200)
        self.editor_client.post(self.photos_url(loc), {"action": "remove", "photo": guest_photo.public_id})
        guest_photo.refresh_from_db()
        self.assertTrue(guest_photo.is_withdrawn)
        self.assertNotEqual(guest.get(photo_url).status_code, 200)
        notice = LocationNotice.objects.get(location=loc, kind=LocationNotice.Kind.PHOTO_REMOVED)
        self.assertEqual(notice.mail_status, LocationNotice.MailStatus.SENT)
        self.assertIn("Foto", mail.outbox[-1].body)
