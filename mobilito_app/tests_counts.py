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

import json
import uuid
from datetime import timedelta

from unittest import mock

from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from authentication.models import MobilitoUser, SignInAttempt
from core.geocoding import GeocodeResult
from core.models import Location, LocationEvidence, PublicationState
from mobilito_app.models import ModalShareCountEvent, ModalShareSession

NANTES = GeocodeResult(
    address="2 Rue de Strasbourg, Nantes",
    country="FR",
    region="Pays de la Loire",
    department="Loire-Atlantique",
    commune="Nantes",
)


def start_data(**overrides):
    data = {
        "lat": "47.218400",
        "lon": "-1.553600",
        "device_lat": "47.218450",
        "device_lon": "-1.553650",
        "device_accuracy": "9",
        "address": "2 Rue de Strasbourg, Nantes",
    }
    data.update(overrides)
    return data


class CountFlowTestCase(TestCase):
    """Base: a signed-in, validated observer unless a test says not."""

    def setUp(self):
        cache.clear()
        patcher = mock.patch(
            "core.locations.reverse_geocode", return_value=NANTES
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = MobilitoUser.objects.create_user("counter@example.com")
        self.user.email_validated = True
        self.user.save()
        self.client.force_login(self.user)

    def start(self, **overrides):
        response = self.client.post(
            reverse("counts_start"), start_data(**overrides)
        )
        return response, ModalShareSession.objects.order_by("pk").last()

    def post_json(self, name, session, body):
        return self.client.post(
            reverse(name, args=[session.pk]),
            json.dumps(body),
            content_type="application/json",
        )

    def tap(self, session, mode="car", **extra):
        body = {
            "mode": mode,
            "event_id": str(uuid.uuid4()),
            "client_timestamp": timezone.now().timestamp() * 1000,
        }
        body.update(extra)
        return self.post_json("counts_event", session, body)

    def finish(self, session, totals=None):
        return self.post_json(
            "counts_finish",
            session,
            {
                "totals": totals or {"ped": 0, "bike": 0, "car": 0, "tc": 0},
                "finished_at": timezone.now().timestamp() * 1000,
            },
        )


class NewCountTests(CountFlowTestCase):
    def test_nobody_is_asked_for_an_email_first(self):
        self.client.logout()
        response = self.client.get(reverse("counts_new"))
        self.assertEqual(
            response["Location"],
            reverse("auth_observe") + "?next=%2Fcounts%2Fnew%2F",
        )

    def test_renders_map_with_crosshair_and_gps(self):
        response = self.client.get(reverse("counts_new"))
        self.assertContains(response, "mobilito-crosshair")
        self.assertContains(response, "data-map-gps")
        self.assertContains(response, "Start counting")

    def test_explains_what_counts_as_what(self):
        response = self.client.get(reverse("counts_new"))
        self.assertContains(response, "What counts as what?")
        self.assertContains(response, "One tap for each thing that goes by")
        self.assertContains(
            response, '<details class="mobilito-what-counts card mt-3">'
        )
        self.assertContains(response, "kick scooters")

    def test_provisional_observer_can_start(self):
        self.client.logout()
        self.client.post(
            reverse("auth_observe"), {"email": "walker@example.com"}
        )
        response = self.client.get(reverse("counts_new"))
        self.assertEqual(response.status_code, 200)


class StartCountTests(CountFlowTestCase):
    def test_must_confirm_location_first(self):
        response = self.client.post(reverse("counts_start"), {"address": "x"})
        self.assertContains(
            response, "Confirm location” first", status_code=400
        )
        self.assertFalse(ModalShareSession.objects.exists())

    def test_creates_draft_session_location_and_evidence(self):
        response, session = self.start()
        self.assertRedirects(
            response, reverse("counts_count", args=[session.pk])
        )
        self.assertEqual(session.user, self.user)
        self.assertEqual(session.publication_state, PublicationState.DRAFT)
        self.assertFalse(session.location_mismatch)
        location = session.location
        self.assertAlmostEqual(location.point.y, 47.2184)
        self.assertEqual(location.commune, "Nantes")
        self.assertEqual(location.reverse_geocoded_address, NANTES.address)
        # Same as the suggestion: not recorded as the user's own.
        self.assertEqual(location.user_entered_address, "")
        evidence = LocationEvidence.objects.get()
        self.assertEqual(evidence.observation, session)
        self.assertAlmostEqual(evidence.device_point.y, 47.21845)
        self.assertAlmostEqual(evidence.user_adjusted_point.y, 47.2184)
        self.assertEqual(evidence.accuracy_metres, 9)

    def test_user_edited_address_is_kept(self):
        _, session = self.start(address="Outside the bakery")
        self.assertEqual(
            session.location.user_entered_address, "Outside the bakery"
        )

    def test_vague_fix_near_the_spot_is_not_a_mismatch(self):
        # ~550 m away, but only accurate to 800 m.
        _, session = self.start(
            device_lat="47.2234", device_lon="-1.5536", device_accuracy="800"
        )
        self.assertFalse(session.location_mismatch)

    def test_far_device_flags_mismatch(self):
        _, session = self.start(device_lat="47.30", device_lon="-1.50")
        self.assertTrue(session.location_mismatch)

    def test_no_gps_is_fine(self):
        _, session = self.start(
            device_lat="", device_lon="", device_accuracy=""
        )
        self.assertFalse(session.location_mismatch)
        self.assertIsNone(LocationEvidence.objects.get().device_point)

    def test_cloudflare_position_recorded_as_edge_evidence(self):
        self.client.post(
            reverse("counts_start"),
            start_data(),
            HTTP_CF_IPLATITUDE="47.2",
            HTTP_CF_IPLONGITUDE="-1.55",
        )
        self.assertAlmostEqual(
            LocationEvidence.objects.get().edge_point.y, 47.2
        )

    def test_honeypot(self):
        response = self.client.post(
            reverse("counts_start"), start_data(hp_field="x")
        )
        self.assertRedirects(response, reverse("home"))
        self.assertFalse(ModalShareSession.objects.exists())

    def test_provisional_session_has_no_user(self):
        self.client.logout()
        self.client.post(
            reverse("auth_observe"), {"email": "walker@example.com"}
        )
        _, session = self.start()
        self.assertIsNone(session.user)
        self.assertEqual(session.sign_in_attempt, SignInAttempt.objects.get())

    def test_another_count_here_reuses_the_location(self):
        _, first = self.start()
        _, second = self.start(origin=first.pk, lat="47.21845")
        self.assertEqual(second.location, first.location)

    def test_moved_far_from_origin_gets_a_new_location(self):
        _, first = self.start()
        _, second = self.start(origin=first.pk, lat="47.2200")
        self.assertNotEqual(second.location, first.location)

    def test_origin_not_visible_is_ignored(self):
        stranger = ModalShareSession.objects.create(
            location=Location.objects.create(point=Point(-1.5536, 47.2184)),
            started_at=timezone.now(),
        )
        _, session = self.start(origin=stranger.pk)
        self.assertNotEqual(session.location, stranger.location)

    @override_settings(RATE_LIMIT_COUNT_START=(1, 3600))
    def test_rate_limited(self):
        self.start()
        response, _ = self.start()
        self.assertEqual(response.status_code, 429)


class ResumeTests(CountFlowTestCase):
    def test_start_page_and_home_offer_the_open_count(self):
        _, session = self.start()
        url = reverse("counts_count", args=[session.pk])
        self.assertContains(self.client.get(reverse("counts_new")), url)
        self.assertContains(self.client.get(reverse("home")), url)

    def test_finished_or_old_counts_are_not_offered(self):
        _, session = self.start()
        self.finish(session)
        _, old = self.start()
        ModalShareSession.objects.filter(pk=old.pk).update(
            started_at=timezone.now() - timedelta(hours=7)
        )
        response = self.client.get(reverse("counts_new"))
        self.assertNotContains(response, "count in progress")


class CountScreenTests(CountFlowTestCase):
    def test_owner_gets_the_four_buttons(self):
        _, session = self.start()
        response = self.client.get(reverse("counts_count", args=[session.pk]))
        for mode in ("ped", "bike", "car", "tc"):
            self.assertContains(response, f'data-mode="{mode}"')
        # The guide opens as an overlay, not in the layout.
        self.assertContains(response, "data-guide-open")
        self.assertContains(response, "kick scooters")
        self.assertNotContains(response, "<details")
        self.assertContains(
            response, reverse("counts_event", args=[session.pk])
        )
        # Full screen: no nav, no confirmation banner.
        self.assertNotContains(response, "navbar")
        self.assertNotContains(response, "Please confirm your email")

    def test_other_people_get_404(self):
        _, session = self.start()
        self.client.force_login(
            MobilitoUser.objects.create_user("other@example.com")
        )
        response = self.client.get(reverse("counts_count", args=[session.pk]))
        self.assertEqual(response.status_code, 404)

    def test_finished_session_goes_to_results(self):
        _, session = self.start()
        self.finish(session)
        response = self.client.get(reverse("counts_count", args=[session.pk]))
        self.assertRedirects(
            response, reverse("counts_detail", args=[session.pk])
        )


class RecordEventTests(CountFlowTestCase):
    def test_stores_a_tap(self):
        _, session = self.start()
        response = self.tap(session, "bike", lat=47.2, lon=-1.55)
        self.assertEqual(response.status_code, 204)
        event = ModalShareCountEvent.objects.get()
        self.assertEqual(event.mode, "bike")
        self.assertAlmostEqual(event.point.y, 47.2)

    def test_retry_is_stored_once(self):
        _, session = self.start()
        event_id = str(uuid.uuid4())
        self.tap(session, event_id=event_id)
        response = self.tap(session, event_id=event_id)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(ModalShareCountEvent.objects.count(), 1)

    def test_rejects_unknown_mode_and_bad_ids(self):
        _, session = self.start()
        self.assertEqual(self.tap(session, "horse").status_code, 400)
        self.assertEqual(self.tap(session, event_id="nope").status_code, 400)
        response = self.client.post(
            reverse("counts_event", args=[session.pk]),
            "not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_implausible_phone_clock_uses_server_time(self):
        _, session = self.start()
        self.tap(session, client_timestamp=0)
        event = ModalShareCountEvent.objects.get()
        self.assertLess(
            abs((event.timestamp - timezone.now()).total_seconds()), 60
        )

    def test_taps_after_finish_are_refused(self):
        _, session = self.start()
        self.finish(session)
        self.assertEqual(self.tap(session).status_code, 409)

    def test_other_people_cannot_tap(self):
        _, session = self.start()
        self.client.logout()
        self.assertEqual(self.tap(session).status_code, 302)

    @override_settings(RATE_LIMIT_COUNT_EVENTS_PER_SESSION=(2, 60))
    def test_rate_limited(self):
        _, session = self.start()
        self.tap(session)
        self.tap(session)
        self.assertEqual(self.tap(session).status_code, 429)


class FinishTests(CountFlowTestCase):
    def test_records_totals_and_queues_for_moderation(self):
        _, session = self.start()
        self.tap(session, "car")
        self.tap(session, "ped")
        response = self.finish(
            session, {"ped": 1, "bike": 0, "car": 1, "tc": 0}
        )
        self.assertEqual(
            response.json()["redirect"],
            reverse("counts_detail", args=[session.pk]),
        )
        session.refresh_from_db()
        self.assertEqual(session.total_car, 1)
        self.assertIsNotNone(session.finished_at)
        self.assertEqual(
            session.publication_state, PublicationState.PENDING_MODERATION
        )
        self.assertEqual(
            session.integrity_hash, session.compute_integrity_hash()
        )
        self.assertEqual(session.totals(), session.event_totals())

    def test_provisional_count_waits_for_validation(self):
        self.client.logout()
        self.client.post(
            reverse("auth_observe"), {"email": "walker@example.com"}
        )
        _, session = self.start()
        self.finish(session)
        session.refresh_from_db()
        self.assertEqual(
            session.publication_state, PublicationState.PENDING_VALIDATION
        )

    def test_retried_finish_changes_nothing(self):
        _, session = self.start()
        self.finish(session, {"ped": 3, "bike": 0, "car": 0, "tc": 0})
        response = self.finish(
            session, {"ped": 9, "bike": 0, "car": 0, "tc": 0}
        )
        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.total_pedestrian, 3)

    def test_invalid_totals(self):
        _, session = self.start()
        for totals in (
            {"ped": -1},
            {"ped": "3"},
            {"ped": True},
            {"ped": 10**12},
            "nope",
        ):
            response = self.post_json(
                "counts_finish", session, {"totals": totals}
            )
            self.assertEqual(response.status_code, 400)

    def test_finish_is_never_before_the_last_tap(self):
        _, session = self.start()
        self.tap(session)
        self.post_json(
            "counts_finish",
            session,
            {
                "totals": {"ped": 0, "bike": 0, "car": 1, "tc": 0},
                "finished_at": session.started_at.timestamp() * 1000,
            },
        )
        session.refresh_from_db()
        self.assertGreaterEqual(
            session.finished_at, ModalShareCountEvent.objects.get().timestamp
        )

    def test_integrity_hash_detects_tampering(self):
        _, session = self.start()
        self.tap(session)
        self.finish(session, {"ped": 0, "bike": 0, "car": 1, "tc": 0})
        session.refresh_from_db()
        ModalShareCountEvent.objects.update(mode="bike")
        self.assertNotEqual(
            session.integrity_hash, session.compute_integrity_hash()
        )


class DiscardTests(CountFlowTestCase):
    def test_discard_removes_session_and_unused_location(self):
        _, session = self.start()
        location_id = session.location_id
        self.tap(session)
        response = self.post_json("counts_discard", session, {})
        self.assertEqual(response.json()["redirect"], reverse("home"))
        self.assertFalse(ModalShareSession.objects.exists())
        self.assertFalse(ModalShareCountEvent.objects.exists())
        self.assertFalse(LocationEvidence.objects.exists())
        self.assertFalse(Location.objects.filter(pk=location_id).exists())

    def test_discard_says_so_on_the_next_page(self):
        _, session = self.start()
        self.post_json("counts_discard", session, {})
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Count discarded.")

    def test_discard_keeps_a_location_used_by_another_count(self):
        _, first = self.start()
        self.finish(first)
        _, second = self.start(origin=first.pk)
        self.post_json("counts_discard", second, {})
        self.assertTrue(Location.objects.filter(pk=first.location_id).exists())

    def test_finished_counts_cannot_be_discarded(self):
        _, session = self.start()
        self.finish(session)
        response = self.post_json("counts_discard", session, {})
        self.assertEqual(response.status_code, 409)


class DetailTests(CountFlowTestCase):
    def finished(self, **totals):
        _, session = self.start()
        values = {"ped": 0, "bike": 0, "car": 0, "tc": 0}
        values.update(totals)
        self.finish(session, values)
        session.refresh_from_db()
        return session

    def test_owner_sees_results_and_review_notice(self):
        session = self.finished(ped=3, car=1)
        response = self.client.get(reverse("counts_detail", args=[session.pk]))
        self.assertContains(response, "width: 75%")
        self.assertContains(response, "once it has been reviewed")
        self.assertContains(response, f"?from={session.pk}")
        self.assertNotContains(response, "Link to share")

    def test_unpublished_is_hidden_from_others(self):
        session = self.finished()
        self.client.logout()
        response = self.client.get(reverse("counts_detail", args=[session.pk]))
        self.assertEqual(response.status_code, 404)

    def test_published_is_public_and_shareable(self):
        session = self.finished(bike=2)
        ModalShareSession.objects.filter(pk=session.pk).update(
            publication_state=PublicationState.PUBLISHED
        )
        self.client.logout()
        response = self.client.get(reverse("counts_detail", args=[session.pk]))
        self.assertContains(response, "Link to share")
        self.assertNotContains(response, "once it has been reviewed")

    def test_open_session_owner_goes_back_to_counting(self):
        _, session = self.start()
        response = self.client.get(reverse("counts_detail", args=[session.pk]))
        self.assertRedirects(
            response, reverse("counts_count", args=[session.pk])
        )

    def test_place_without_address_shows_coordinates(self):
        _, session = self.start()
        Location.objects.filter(pk=session.location_id).update(
            user_entered_address="", reverse_geocoded_address=""
        )
        self.finish(session, {"ped": 1, "bike": 0, "car": 0, "tc": 0})
        response = self.client.get(reverse("counts_detail", args=[session.pk]))
        self.assertContains(response, "Near 47.21840, -1.55360")
        self.assertContains(response, "less than a minute")
        self.assertContains(response, "Pedestrians")

    def test_empty_count_says_so(self):
        session = self.finished()
        response = self.client.get(reverse("counts_detail", args=[session.pk]))
        self.assertContains(response, "Nothing was counted")

    def test_zero_in_one_mode_does_not_divide_by_zero(self):
        session = self.finished(car=2)
        response = self.client.get(reverse("counts_detail", args=[session.pk]))
        self.assertContains(response, "width: 0%")
        self.assertContains(response, "width: 100%")

    def test_mismatch_is_noted(self):
        _, session = self.start(device_lat="47.30", device_lon="-1.50")
        self.finish(session)
        response = self.client.get(reverse("counts_detail", args=[session.pk]))
        self.assertContains(response, "some distance from this spot")


class HomeForProvisionalObserverTests(TestCase):
    def test_provisional_observer_sees_observation_buttons(self):
        cache.clear()
        self.client.post(
            reverse("auth_observe"), {"email": "walker@example.com"}
        )
        response = self.client.get(reverse("home"))
        self.assertContains(response, reverse("counts_new"))

    def test_visitor_is_asked_for_email_before_observing(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, reverse("auth_observe") + "?next=/")
