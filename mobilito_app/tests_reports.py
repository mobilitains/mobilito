"""
Copyright 2024  Francais pour une Meilleure Mobilité

Author(s): Jeff Abrahamson <jeff@p27.eu>.

This file is part of the mobilito web application.

Mobilito is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

Mobilito is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public
License along with mobilito.  If not, see
<http://www.gnu.org/licenses/>.
"""

import os
import tempfile
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from authentication.models import MobilitoUser, SignInAttempt
from core.geocoding import GeocodeResult
from core.models import LocationEvidence, PublicationState
from core.tests import make_photo
from mobilito_app.models import (
    InfrastructureMedia,
    InfrastructureObservation,
    InfrastructureTag,
    TagStatus,
)

NANTES = GeocodeResult(
    address="2 Rue de Strasbourg, Nantes",
    country="FR",
    region="Pays de la Loire",
    department="Loire-Atlantique",
    commune="Nantes",
)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ReportTestCase(TestCase):
    def setUp(self):
        cache.clear()
        for target in ("core.locations", "core.views"):
            patcher = mock.patch(
                f"{target}.reverse_geocode", return_value=NANTES
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        self.user = MobilitoUser.objects.create_user("reporter@example.com")
        self.user.email_validated = True
        self.user.save()
        self.client.force_login(self.user)
        self.universal = InfrastructureTag.objects.create(label="Dark")
        self.french = InfrastructureTag.objects.create(
            label="Zone 30", country="FR"
        )
        self.belgian = InfrastructureTag.objects.create(
            label="Fietsstraat", country="BE"
        )
        self.old = InfrastructureTag.objects.create(
            label="Old", status=TagStatus.DEPRECATED
        )

    def submit(self, photos=None, **overrides):
        data = {
            "lat": "47.218400",
            "lon": "-1.553600",
            "device_lat": "47.218450",
            "device_lon": "-1.553650",
            "device_accuracy": "9",
            "address": "2 Rue de Strasbourg, Nantes",
            "perspective": "bike",
            "description": "Cycle lane blocked by parked cars",
            "tags": [self.universal.pk, self.french.pk],
        }
        data.update(overrides)
        data["photos"] = (
            [make_photo(gps=(47.2185, -1.5537))] if photos is None else photos
        )
        return self.client.post(reverse("reports_submit"), data)


class NewReportTests(ReportTestCase):
    def test_nobody_is_asked_for_an_email_first(self):
        self.client.logout()
        response = self.client.get(reverse("reports_new"))
        self.assertTrue(
            response["Location"].startswith(reverse("auth_observe"))
        )

    def test_page_has_map_and_all_steps(self):
        response = self.client.get(reverse("reports_new"))
        self.assertContains(response, "mobilito-crosshair")
        self.assertContains(
            response, f'hx-post="{reverse("reports_location")}"'
        )
        self.assertContains(response, 'name="perspective"', count=3)
        self.assertContains(response, 'name="photos"')
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, "Confirm the location to see the tags")

    def test_home_links_here(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, reverse("reports_new"))


class ConfirmLocationTests(ReportTestCase):
    def test_offers_universal_and_local_tags_only(self):
        response = self.client.post(
            reverse("reports_location"),
            {"widget": "report-map", "map-lat": "47.2", "map-lon": "-1.5"},
        )
        self.assertContains(response, "Location confirmed.")
        self.assertContains(response, 'id="report-tags" hx-swap-oob="true"')
        self.assertContains(response, "Zone 30")
        self.assertContains(response, "Dark")
        self.assertNotContains(response, "Fietsstraat")
        self.assertNotContains(response, "Old")

    def test_ticked_tags_survive_reconfirmation(self):
        response = self.client.post(
            reverse("reports_location"),
            {
                "widget": "report-map",
                "map-lat": "47.2",
                "map-lon": "-1.5",
                "tags": [str(self.french.pk)],
            },
        )
        self.assertRegex(
            response.content.decode(),
            rf'value="{self.french.pk}" id="tag-{self.french.pk}"\s+checked',
        )

    def test_invalid_position_leaves_tags_alone(self):
        response = self.client.post(
            reverse("reports_location"), {"widget": "report-map"}
        )
        self.assertNotContains(response, "hx-swap-oob")


class SubmitReportTests(ReportTestCase):
    def test_creates_report_photos_tags_and_evidence(self):
        response = self.submit()
        report = InfrastructureObservation.objects.get()
        self.assertRedirects(
            response, reverse("reports_detail", args=[report.pk])
        )
        self.assertEqual(report.user, self.user)
        self.assertEqual(report.observer_perspective, "bike")
        self.assertEqual(
            report.publication_state, PublicationState.PENDING_MODERATION
        )
        self.assertEqual(set(report.tags.all()), {self.universal, self.french})
        self.assertEqual(report.location.country, "FR")
        media = report.media.get()
        self.assertEqual(media.added_by, self.user)
        self.assertAlmostEqual(media.exif_point.y, 47.2185, places=3)
        self.assertTrue(media.file.name.endswith(".jpg"))
        evidence = LocationEvidence.objects.get()
        self.assertAlmostEqual(evidence.exif_point.y, 47.2185, places=3)
        self.assertAlmostEqual(evidence.device_point.y, 47.21845)

    def test_several_photos(self):
        self.submit(photos=[make_photo(), make_photo(), make_photo()])
        self.assertEqual(InfrastructureMedia.objects.count(), 3)

    def test_photo_required(self):
        response = self.submit(photos=[])
        self.assertContains(
            response, "Add at least one photo", status_code=400
        )
        self.assertFalse(InfrastructureObservation.objects.exists())

    @override_settings(PHOTO_MAX_PER_REPORT=2)
    def test_too_many_photos(self):
        response = self.submit(photos=[make_photo()] * 3)
        self.assertContains(response, "At most 2 photos", status_code=400)

    def test_non_image_is_refused_with_its_name(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = self.submit(
            photos=[SimpleUploadedFile("notes.txt", b"hello")]
        )
        self.assertContains(response, "notes.txt", status_code=400)
        self.assertFalse(InfrastructureObservation.objects.exists())

    def test_perspective_required(self):
        response = self.submit(perspective="")
        self.assertContains(
            response, "Choose how you usually pass here", status_code=400
        )

    def test_tag_from_another_country_is_refused(self):
        response = self.submit(tags=[self.belgian.pk])
        self.assertContains(
            response, "Choose tags from the list shown", status_code=400
        )

    def test_deprecated_tag_is_refused(self):
        response = self.submit(tags=[self.old.pk])
        self.assertEqual(response.status_code, 400)

    def test_errors_keep_the_confirmed_location_and_entries(self):
        response = self.submit(photos=[], description="Keep me")
        self.assertContains(response, "Location confirmed.", status_code=400)
        self.assertContains(
            response, 'name="lat" value="47.218400"', status_code=400
        )
        self.assertContains(response, "Keep me", status_code=400)
        self.assertContains(
            response, "Please add your photos again", status_code=400
        )
        # Tags for the confirmed country, with the ticked ones kept.
        self.assertContains(response, "Zone 30", status_code=400)

    def test_must_confirm_location_first(self):
        response = self.submit(lat="", lon="")
        self.assertContains(
            response, "Confirm location” first", status_code=400
        )

    def test_same_form_sent_twice_makes_one_report(self):
        key = "7f1b1f7e-2a53-4a3e-9d35-0d1c3f1e2a10"
        self.submit(submission_id=key)
        report = InfrastructureObservation.objects.get()
        response = self.submit(submission_id=key)
        self.assertRedirects(
            response,
            reverse("reports_detail", args=[report.pk]),
            fetch_redirect_response=False,
        )
        self.assertEqual(InfrastructureObservation.objects.count(), 1)
        self.assertContains(
            self.client.get(response.url), "nothing new was saved"
        )

    def test_same_form_sent_twice_at_once(self):
        # Both requests pass the checks before either has saved; the
        # second to save hits the unique key and shows the first. (In a
        # TestCase this exercises the savepoint rollback, not a real
        # concurrent commit.)
        key = "7f1b1f7e-2a53-4a3e-9d35-0d1c3f1e2a10"
        self.submit(submission_id=key)
        report = InfrastructureObservation.objects.get()
        media_files = set(
            InfrastructureMedia.objects.values_list("file", flat=True)
        )
        with mock.patch(
            "mobilito_app.reports._already_sent", side_effect=[None, report]
        ), mock.patch("mobilito_app.reports._key_in_use", return_value=False):
            response = self.submit(submission_id=key)
        self.assertRedirects(
            response,
            reverse("reports_detail", args=[report.pk]),
            fetch_redirect_response=False,
        )
        self.assertEqual(InfrastructureObservation.objects.count(), 1)
        self.assertEqual(
            set(InfrastructureMedia.objects.values_list("file", flat=True)),
            media_files,
        )

    def test_provisional_same_form_twice_makes_one_report(self):
        self.client.logout()
        self.client.post(
            reverse("auth_observe"), {"email": "walker@example.com"}
        )
        key = "7f1b1f7e-2a53-4a3e-9d35-0d1c3f1e2a10"
        self.submit(submission_id=key)
        self.submit(submission_id=key)
        self.assertEqual(InfrastructureObservation.objects.count(), 1)

    def test_error_page_keeps_the_forms_key(self):
        key = "7f1b1f7e-2a53-4a3e-9d35-0d1c3f1e2a10"
        response = self.submit(submission_id=key, perspective="")
        self.assertContains(response, f'value="{key}"', status_code=400)

    def test_unreadable_key_is_ignored(self):
        response = self.submit(submission_id="not-a-uuid")
        self.assertEqual(response.status_code, 302)
        report = InfrastructureObservation.objects.get()
        self.assertIsNone(report.client_submission_id)

    def test_someone_elses_form_key_is_not_theirs(self):
        key = "7f1b1f7e-2a53-4a3e-9d35-0d1c3f1e2a10"
        self.submit(submission_id=key)
        other = MobilitoUser.objects.create_user("other@example.com")
        other.email_validated = True
        other.save()
        self.client.force_login(other)
        self.submit(submission_id=key)
        self.assertEqual(InfrastructureObservation.objects.count(), 2)
        self.assertEqual(
            InfrastructureObservation.objects.filter(
                client_submission_id=key
            ).count(),
            1,
        )

    def test_form_carries_a_submission_id(self):
        response = self.client.get(reverse("reports_new"))
        self.assertContains(response, 'name="submission_id"')
        first = response.context["form"].initial["submission_id"]
        second = self.client.get(reverse("reports_new")).context["form"]
        self.assertNotEqual(first, second.initial["submission_id"])

    def test_get_goes_back_to_the_form(self):
        response = self.client.get(reverse("reports_submit"))
        self.assertRedirects(response, reverse("reports_new"))

    def test_honeypot(self):
        response = self.submit(hp_field="x")
        self.assertRedirects(response, reverse("home"))
        self.assertFalse(InfrastructureObservation.objects.exists())

    def test_honeypot_with_other_errors(self):
        response = self.submit(hp_field="x", perspective="", photos=[])
        self.assertRedirects(response, reverse("home"))

    def test_tags_kept_if_the_second_geocode_fails(self):
        # Confirm time showed the French tags; the lookup at submit
        # fails (no country): they're not refused for that.
        with mock.patch("core.locations.reverse_geocode", return_value=None):
            response = self.submit()
        self.assertEqual(response.status_code, 302)
        report = InfrastructureObservation.objects.get()
        self.assertEqual(report.tags.count(), 2)

    def test_failure_leaves_no_photo_files(self):
        def files():
            return sum(
                len(names) for _, _, names in os.walk(settings.MEDIA_ROOT)
            )

        before = files()
        with mock.patch(
            "mobilito_app.reports.LocationEvidence.objects.create",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                self.submit(photos=[make_photo(), make_photo()])
        self.assertFalse(InfrastructureObservation.objects.exists())
        self.assertEqual(files(), before)

    def test_provisional_report_waits_for_validation(self):
        self.client.logout()
        self.client.post(
            reverse("auth_observe"), {"email": "walker@example.com"}
        )
        self.submit()
        report = InfrastructureObservation.objects.get()
        self.assertIsNone(report.user)
        self.assertEqual(report.sign_in_attempt, SignInAttempt.objects.get())
        self.assertEqual(
            report.publication_state, PublicationState.PENDING_VALIDATION
        )
        self.assertIsNone(report.media.get().added_by)
        detail = self.client.get(reverse("reports_detail", args=[report.pk]))
        self.assertContains(detail, "opened the link we sent you")

    @override_settings(RATE_LIMIT_REPORT_SUBMIT=(1, 3600))
    def test_rate_limited(self):
        self.submit()
        response = self.submit()
        self.assertContains(
            response, "wait a while and try again", status_code=429
        )
        # A fresh key: this page is a new form.
        self.assertTrue(response.context["form"].initial["submission_id"])


class DetailAndPhotoTests(ReportTestCase):
    def setUp(self):
        super().setUp()
        self.submit()
        self.report = InfrastructureObservation.objects.get()
        self.media = self.report.media.get()
        self.detail_url = reverse("reports_detail", args=[self.report.pk])
        self.photo_url = reverse(
            "reports_photo", args=[self.report.pk, self.media.pk]
        )

    def test_owner_sees_report_and_photo(self):
        response = self.client.get(self.detail_url)
        self.assertContains(response, "Cycle lane blocked")
        self.assertContains(response, "Zone 30")
        self.assertContains(response, self.photo_url)
        self.assertContains(response, "once it has been reviewed")
        self.assertContains(response, "data-report-sent")
        photo = self.client.get(self.photo_url)
        self.assertEqual(photo["Content-Type"], "image/jpeg")
        self.assertIn("private", photo["Cache-Control"])
        self.assertTrue(
            b"".join(photo.streaming_content).startswith(b"\xff\xd8")
        )

    def test_missing_photo_file_is_not_found(self):
        self.media.file.delete(save=False)
        self.assertEqual(self.client.get(self.photo_url).status_code, 404)

    def test_unpublished_is_hidden_from_others(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.detail_url).status_code, 404)
        self.assertEqual(self.client.get(self.photo_url).status_code, 404)

    def test_published_is_public(self):
        InfrastructureObservation.objects.update(
            publication_state=PublicationState.PUBLISHED
        )
        self.client.logout()
        response = self.client.get(self.detail_url)
        self.assertContains(response, "Link to share")
        self.assertNotContains(response, "data-report-sent")
        self.assertEqual(self.client.get(self.photo_url).status_code, 200)

    def test_unpublished_photo_of_published_report_is_hidden(self):
        InfrastructureObservation.objects.update(
            publication_state=PublicationState.PUBLISHED
        )
        InfrastructureMedia.objects.update(published=False)
        self.client.logout()
        self.assertNotContains(
            self.client.get(self.detail_url), self.photo_url
        )
        self.assertEqual(self.client.get(self.photo_url).status_code, 404)

    def test_photo_must_belong_to_the_report(self):
        self.submit()
        other = InfrastructureObservation.objects.exclude(
            pk=self.report.pk
        ).get()
        url = reverse("reports_photo", args=[other.pk, self.media.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
