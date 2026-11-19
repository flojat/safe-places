"""Erfahrungsberichte: Erfassen durch Mitglieder/Redaktion, Hinweis an die Redaktion, Freigabe nur
durch die Redaktion, Mitteilung an die Objektbetreuung, Sichtbarkeit, Fotos (max. 3) mit
Publikationsberechtigung."""

import os

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from locations.models import AuditEvent, ExperienceReport, ExperienceReportPhoto, LocationNotice
from locations.roles import EDITOR, OBJECT_MANAGER
from locations.services import experience_reports, images, publishing

from .test_stage3_member import image_bytes, make_published
from .test_stage4_work import verified

REPORT_SUBJECT = "Adresskarte: Neuer Erfahrungsbericht zur Pruefung"


def jpeg(name="x.jpg"):
    return SimpleUploadedFile(name, image_bytes(), content_type="image/jpeg")


def report_mails():
    return [m for m in mail.outbox if m.subject == REPORT_SUBJECT]


class ReportBase(TestCase):
    def setUp(self):
        self.owner_client, self.owner = verified("betreuung@example.org", groups=[OBJECT_MANAGER])
        self.editor_client, self.editor = verified("redaktion@example.org", groups=[EDITOR], staff=True)
        self.loc, self.pub, _ = make_published(1, photos=False, managed_by=self.owner)
        self.member, self.member_user = verified("mitglied@example.org")
        self.create_url = reverse("locations:report_create", args=[self.pub.public_id])
        self.detail_url = reverse("locations:preview", args=[self.pub.public_id])
        mail.outbox.clear()

    def submit(self, client=None, text="CANARY-BERICHT Rampe war gut befahrbar.", **extra):
        data = {"text": text}
        data.update(extra)
        return (client or self.member).post(self.create_url, data)

    def open_details(self, client=None):
        (client or self.member).post(reverse("locations:open_details", args=[self.pub.public_id]))

    def review_url(self, report):
        return reverse("admin:locations_experiencereport_change", args=[report.pk])


class SubmitTests(ReportBase):
    def test_guest_and_password_only_cannot_open_form(self):
        self.assertNotEqual(Client().get(self.create_url).status_code, 200)
        self.assertNotEqual(Client().post(self.create_url, {"text": "x"}).status_code, 200)
        self.assertFalse(ExperienceReport.objects.exists())

    def test_member_form_shows_photo_suggestion_and_consent(self):
        page = self.member.get(self.create_url).content.decode()
        self.assertIn("Überblick", page)
        self.assertIn("zwei Fotos mit Details", page)
        self.assertIn("ausschliesslich auf dieser Webseite", page)
        self.assertIn("keine fremden Bildrechte", page)
        self.assertIn('maxlength="2000"', page)
        self.assertNotIn("MITGLIEDERDETAIL", page)  # Formular gibt nur Gastfelder aus

    def test_member_submission_is_pending_and_notifies_editors_without_content_or_links(self):
        r = self.submit()
        self.assertRedirects(r, self.detail_url, fetch_redirect_response=False)
        report = ExperienceReport.objects.get()
        self.assertEqual(report.state, ExperienceReport.State.SUBMITTED)
        self.assertEqual(report.author, self.member_user)
        self.assertEqual(report.location, self.loc)
        self.assertEqual(report.editor_mail_status, ExperienceReport.MailStatus.SENT)
        mails = report_mails()
        self.assertEqual([m.to for m in mails], [[self.editor.email]])
        body = mails[0].body
        self.assertIn(report.public_id, body)
        self.assertIn(self.loc.title, body)
        self.assertNotIn("CANARY-BERICHT", body)
        self.assertNotIn("http", body)
        self.assertNotIn(self.member_user.email, body)
        self.assertTrue(AuditEvent.objects.filter(action="report_submit", target_id=report.public_id).exists())
        self.assertContains(self.member.get(self.detail_url), "von der Redaktion geprüft")

    def test_editor_submission_sends_no_editor_mail(self):
        self.submit(self.editor_client)
        report = ExperienceReport.objects.get()
        self.assertEqual(report.state, ExperienceReport.State.SUBMITTED)
        self.assertEqual(report.editor_mail_status, ExperienceReport.MailStatus.NONE)
        self.assertEqual(report_mails(), [])

    def test_without_editors_admins_are_notified(self):
        self.editor.groups.clear()
        _c, admin_user = verified("admin@example.org", superuser=True)
        mail.outbox.clear()
        self.submit()
        self.assertEqual([m.to for m in report_mails()], [[admin_user.email]])

    def test_text_limit_2000_characters_counts_line_breaks_once(self):
        ok = ("a" * 999 + "\r\n") * 2  # 2000 Zeichen nach Normalisierung
        self.submit(text=ok[:-2] + "b")
        self.assertEqual(len(ExperienceReport.objects.get().text), 2000)
        r = self.submit(text="x" * 2001)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(ExperienceReport.objects.count(), 1)
        self.assertContains(r, "2000")

    def test_empty_text_rejected(self):
        self.assertEqual(self.submit(text="   ").status_code, 200)
        self.assertFalse(ExperienceReport.objects.exists())

    def test_three_photos_with_consent(self):
        self.submit(photo_overview=jpeg(), photo_overview_alt="Gesamtansicht",
                    photo_detail_1=jpeg(), photo_detail_2=jpeg(), photo_consent="on")
        report = ExperienceReport.objects.get()
        self.assertIsNotNone(report.photo_consent_at)
        kinds = list(report.photos.values_list("kind", flat=True))
        self.assertEqual(kinds, ["overview", "detail", "detail"])
        self.assertEqual(report.photos.first().alt_text, "Gesamtansicht")
        for p in report.photos.all():
            self.assertIsNotNone(p.rights_confirmed_at)
            self.assertTrue(images.variant_path(p.storage_key, "thumb").exists())

    def test_photos_without_consent_are_refused_and_nothing_stored(self):
        r = self.submit(photo_detail_1=jpeg())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "diese Bestätigung erforderlich")
        self.assertFalse(ExperienceReport.objects.exists())

    def test_invalid_image_rolls_back_including_already_processed_files(self):
        before = set(_media_keys())
        r = self.submit(photo_overview=jpeg(), photo_consent="on",
                        photo_detail_1=SimpleUploadedFile("x.jpg", b"<svg/>", content_type="image/jpeg"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(ExperienceReport.objects.exists())
        self.assertEqual(set(_media_keys()), before)

    def test_service_limits_photo_set(self):
        P = experience_reports.PhotoInput
        with self.assertRaises(experience_reports.ReportRejected):
            experience_reports.validate_photo_set([P("overview", jpeg()), P("overview", jpeg())])
        with self.assertRaises(experience_reports.ReportRejected):
            experience_reports.validate_photo_set([P("detail", jpeg()) for _ in range(4)])

    def test_not_possible_for_withdrawn_object(self):
        publishing.withdraw(self.loc.pk, None)
        self.assertEqual(self.member.get(self.create_url).status_code, 404)
        self.assertEqual(self.submit().status_code, 404)
        self.assertFalse(ExperienceReport.objects.exists())

    def test_submission_rate_limited_per_account(self):
        for _ in range(3):
            self.submit()
        self.assertEqual(self.submit().status_code, 429)
        self.assertEqual(ExperienceReport.objects.count(), 3)


def _media_keys():
    from django.conf import settings

    for _root, dirs, _files in os.walk(settings.PRIVATE_MEDIA_ROOT):
        yield from (d for d in dirs if len(d) == 32)


class ReviewAndVisibilityTests(ReportBase):
    def setUp(self):
        super().setUp()
        self.submit(photo_overview=jpeg(), photo_detail_1=jpeg(), photo_consent="on")
        self.report = ExperienceReport.objects.get()
        self.photo = self.report.photos.first()
        self.photo_url = reverse("locations:report_photo", args=[self.photo.public_id, "detail"])
        mail.outbox.clear()

    def publish(self, client=None):
        return (client or self.editor_client).post(self.review_url(self.report),
                                                    {"action": "publish", "text": self.report.text})

    def test_pending_report_is_invisible_to_members_and_guests(self):
        self.open_details()
        self.assertNotContains(self.member.get(self.detail_url), "CANARY-BERICHT")
        self.assertNotContains(Client().get(self.detail_url), "CANARY-BERICHT")
        other, _ = verified("andere@example.org")
        self.open_details(other)
        self.assertEqual(other.get(self.photo_url).status_code, 404)
        self.assertNotEqual(Client().get(self.photo_url).status_code, 200)
        # Autor und Redaktion sehen die Fotos des eingereichten Berichts.
        self.assertEqual(self.member.get(self.photo_url).status_code, 200)
        self.assertEqual(self.editor_client.get(self.photo_url).status_code, 200)

    def test_only_editors_can_review(self):
        self.assertEqual(self.editor_client.get(self.review_url(self.report)).status_code, 200)
        for client in (self.owner_client, self.member, Client()):
            self.assertNotEqual(client.get(self.review_url(self.report)).status_code, 200)
            self.publish(client)
        self.report.refresh_from_db()
        self.assertEqual(self.report.state, ExperienceReport.State.SUBMITTED)
        self.assertNotEqual(self.owner_client.get(
            reverse("admin:locations_experiencereport_changelist")).status_code, 200)

    def test_publish_makes_visible_to_members_with_grant_and_informs_owner(self):
        self.publish()
        self.report.refresh_from_db()
        self.assertEqual(self.report.state, ExperienceReport.State.PUBLISHED)
        self.assertIsNotNone(self.report.published_at)
        # Objektbetreuung: Mitteilung im Portal + Mail
        notice = LocationNotice.objects.get(location=self.loc, kind=LocationNotice.Kind.REPORT_PUBLISHED)
        self.assertEqual(notice.recipient, self.owner)
        self.assertEqual(notice.mail_status, LocationNotice.MailStatus.SENT)
        owner_mails = [m for m in mail.outbox if m.to == [self.owner.email]]
        self.assertEqual(len(owner_mails), 1)
        self.assertIn("Erfahrungsbericht", owner_mails[0].body)
        self.assertNotIn("CANARY-BERICHT", owner_mails[0].body)
        portal = self.owner_client.get(reverse("portal:edit", args=[self.loc.public_id]))
        self.assertContains(portal, "Neuer Erfahrungsbericht")
        # Mitglied mit Buchung sieht Text und Fotos, ohne Autorenangabe.
        other, _ = verified("andere@example.org")
        self.assertNotContains(other.get(self.detail_url), "CANARY-BERICHT")  # ohne Buchung
        self.assertEqual(other.get(self.photo_url).status_code, 404)
        self.open_details(other)
        page = other.get(self.detail_url).content.decode()
        self.assertIn("CANARY-BERICHT", page)
        self.assertIn(self.photo.public_id, page)
        self.assertNotIn(self.member_user.email, page)
        self.assertEqual(other.get(self.photo_url).status_code, 200)
        # Gaeste nie
        self.assertNotContains(Client().get(self.detail_url), "CANARY-BERICHT")
        self.assertNotEqual(Client().get(self.photo_url).status_code, 200)

    def test_owner_is_informed_only_on_first_publication(self):
        self.publish()
        self.editor_client.post(self.review_url(self.report), {"action": "withdraw"})
        self.report.refresh_from_db()
        self.publish()
        self.assertEqual(LocationNotice.objects.filter(kind=LocationNotice.Kind.REPORT_PUBLISHED).count(), 1)

    def test_withdraw_hides_immediately(self):
        self.publish()
        self.open_details()
        self.assertContains(self.member.get(self.detail_url), "CANARY-BERICHT")
        other, _ = verified("andere@example.org")
        self.open_details(other)
        self.editor_client.post(self.review_url(self.report), {"action": "withdraw"})
        self.assertNotContains(other.get(self.detail_url), "CANARY-BERICHT")
        self.assertEqual(other.get(self.photo_url).status_code, 404)

    def test_object_withdrawal_hides_reports(self):
        self.publish()
        publishing.withdraw(self.loc.pk, None)
        self.assertEqual(experience_reports.published_reports(self.loc.published), [])

    def test_reject_deletes_photo_files(self):
        key = self.photo.storage_key
        with self.captureOnCommitCallbacks(execute=True):
            self.editor_client.post(self.review_url(self.report), {"action": "reject", "feedback": "Personendaten"})
        self.report.refresh_from_db()
        self.assertEqual(self.report.state, ExperienceReport.State.REJECTED)
        self.assertFalse(images.storage_dir(key).exists())
        self.assertTrue(all(p.is_removed for p in self.report.photos.all()))
        self.assertEqual(self.member.get(self.photo_url).status_code, 404)
        self.publish()  # Zurueckgewiesen bleibt zurueckgewiesen
        self.report.refresh_from_db()
        self.assertEqual(self.report.state, ExperienceReport.State.REJECTED)

    def test_editor_can_correct_text_and_remove_single_photo(self):
        self.editor_client.post(self.review_url(self.report), {
            "action": "publish", "text": "Korrigiert: Rampe gut befahrbar.", "remove_photo": [self.photo.public_id],
        })
        self.report.refresh_from_db()
        self.assertEqual(self.report.text, "Korrigiert: Rampe gut befahrbar.")
        self.assertEqual(self.report.state, ExperienceReport.State.PUBLISHED)
        self.assertTrue(ExperienceReportPhoto.objects.get(pk=self.photo.pk).is_removed)
        self.assertTrue(AuditEvent.objects.filter(action="report_edit").exists())
        self.open_details()
        page = self.member.get(self.detail_url).content.decode()
        self.assertNotIn(self.photo.public_id, page)
        self.assertEqual(len(experience_reports.published_reports(self.pub)[0]["gallery"]), 1)
