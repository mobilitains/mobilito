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

import uuid
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from authentication.models import MobilitoUser, SignInAttempt
from core.models import PublicationState as State
from mobilito_app.counts import count_deadline
from mobilito_app.models import (
    InfrastructureObservation,
    ModalShareCountEvent,
    ModalShareSession,
)
from mobilito_app.tests_browse import count_at, report_at


class MyObservationsTests(TestCase):
    def setUp(self):
        self.user = MobilitoUser.objects.create_user("me@example.com")
        self.user.email_validated = True
        self.user.save()
        self.client.force_login(self.user)

    def get(self, **params):
        return self.client.get(reverse("my_observations"), params)

    def test_lists_only_mine_with_plain_states(self):
        other = MobilitoUser.objects.create_user("them@example.com")
        report_at(47.2, -1.5, user=other, description="Theirs")
        report_at(
            47.2,
            -1.5,
            user=self.user,
            state=State.PUBLISHED,
            description="Mine, public",
        )
        report_at(
            47.2,
            -1.5,
            user=self.user,
            state=State.PENDING_MODERATION,
            description="Mine, being checked",
        )
        report_at(
            47.2,
            -1.5,
            user=self.user,
            state=State.SANDBOXED,
            description="Mine, hidden",
        )
        response = self.get()
        self.assertNotContains(response, "Theirs")
        self.assertContains(response, "Visible to everyone")
        self.assertContains(response, "Waiting to be checked")
        self.assertContains(response, "Not visible to others")
        self.assertContains(response, "made 3 observations.")
        self.assertContains(response, "1 is visible to everyone.")
        self.assertNotContains(response, "since you gave your email")
        # Never the internal state names (design §14).
        for word in ("Sandboxed", "moderation", "Pending", "Light hold"):
            self.assertNotContains(response, word)

    def test_counts_open_stale_and_finished(self):
        finished = count_at(47.2, -1.5, user=self.user, total_car=5)
        open_now = count_at(
            47.2, -1.5, user=self.user, finished=False, state=State.DRAFT
        )
        stale = count_at(
            47.2, -1.5, user=self.user, finished=False, state=State.DRAFT
        )
        ModalShareSession.objects.filter(pk=stale.pk).update(
            started_at=timezone.now() - timedelta(days=2)
        )
        response = self.get()
        self.assertContains(response, "5 counted")
        self.assertContains(
            response, reverse("counts_detail", args=[finished.pk])
        )
        self.assertContains(response, "Still counting")
        self.assertContains(
            response, reverse("counts_count", args=[open_now.pk])
        )
        self.assertContains(response, "Not finished")
        self.assertContains(response, "decide what to do with it")
        self.assertContains(response, "Open it to carry on counting.")

    def test_provisional_observer_sees_theirs(self):
        self.client.logout()
        self.client.post(
            reverse("auth_observe"), {"email": "walker@example.com"}
        )
        report = report_at(
            47.2, -1.5, state=State.PENDING_VALIDATION, description="Mine"
        )
        InfrastructureObservation.objects.filter(pk=report.pk).update(
            sign_in_attempt=SignInAttempt.objects.get()
        )
        response = self.get()
        self.assertContains(response, "Mine")
        self.assertContains(response, "Open the link we emailed you")
        # This browser's sign-in only: say so.
        self.assertContains(response, "since you gave your email address")

    def test_an_attempt_for_someone_elses_address_sees_nothing_of_theirs(
        self,
    ):
        report_at(47.2, -1.5, user=self.user, description="Private")
        count_at(47.2, -1.5, user=self.user, finished=False, state=State.DRAFT)
        report_at(
            47.2,
            -1.5,
            user=self.user,
            state=State.SANDBOXED,
            description="Hidden",
        )
        self.client.logout()
        # A stranger types this user's address.
        self.client.post(reverse("auth_observe"), {"email": self.user.email})
        response = self.get()
        self.assertContains(response, "Nothing here yet.")
        self.assertNotContains(response, "Private")
        self.assertNotContains(response, "Hidden")

    def test_signed_in_with_someone_elses_attempt_sees_only_their_own(self):
        other = MobilitoUser.objects.create_user("them@example.com")
        self.client.logout()
        self.client.post(reverse("auth_observe"), {"email": other.email})
        attempt = SignInAttempt.objects.get()
        theirs = report_at(47.2, -1.5, description="Theirs")
        InfrastructureObservation.objects.filter(pk=theirs.pk).update(
            sign_in_attempt=attempt
        )
        self.client.force_login(self.user)
        report_at(47.2, -1.5, user=self.user, description="Mine")
        response = self.get()
        self.assertContains(response, "Mine")
        self.assertNotContains(response, "Theirs")

    def test_all_states_have_plain_words(self):
        for state in State:
            report_at(47.2, -1.5, user=self.user, state=state)
        response = self.get()
        for words in (
            "Not sent yet",
            "Waiting to be checked",
            "Open the link we emailed you",
            "Visible to everyone",
            "Not visible to others",
        ):
            self.assertContains(response, words)

    def test_newest_first_across_kinds(self):
        report = report_at(47.2, -1.5, user=self.user)
        count = count_at(47.2, -1.5, user=self.user)
        ModalShareSession.objects.filter(pk=count.pk).update(
            started_at=report.created_at + timedelta(minutes=1)
        )
        older = report_at(47.2, -1.5, user=self.user)
        InfrastructureObservation.objects.filter(pk=older.pk).update(
            created_at=report.created_at - timedelta(days=1)
        )
        items = [e["item"].pk for e in self.get().context["items"]]
        self.assertEqual(items, [count.pk, report.pk, older.pk])

    def test_nobody_is_sent_to_sign_in(self):
        self.client.logout()
        response = self.get()
        self.assertRedirects(
            response,
            reverse("auth_start") + "?next=" + reverse("my_observations"),
            fetch_redirect_response=False,
        )

    def test_empty(self):
        response = self.get()
        self.assertContains(response, "Nothing here yet.")
        self.assertContains(response, reverse("counts_new"))

    def test_pages(self):
        for _ in range(21):
            report_at(47.2, -1.5, user=self.user)
        response = self.get()
        self.assertEqual(len(response.context["items"]), 20)
        self.assertContains(response, "?page=2")
        self.assertEqual(len(self.get(page="2").context["items"]), 1)

    def test_home_links_here(self):
        self.assertContains(
            self.client.get(reverse("home")), reverse("my_observations")
        )


class StaleCountTests(TestCase):
    """A count left open past the resume window (counts.is_stale)."""

    def setUp(self):
        self.user = MobilitoUser.objects.create_user("me@example.com")
        self.user.email_validated = True
        self.user.save()
        self.client.force_login(self.user)
        self.session = count_at(
            47.2, -1.5, user=self.user, finished=False, state=State.DRAFT
        )
        self.started = timezone.now() - timedelta(days=2)
        ModalShareSession.objects.filter(pk=self.session.pk).update(
            started_at=self.started
        )
        self.session.refresh_from_db()
        self.url = reverse("counts_close_stale", args=[self.session.pk])

    def tap(self, mode, minutes):
        ModalShareCountEvent.objects.create(
            session=self.session,
            mode=mode,
            timestamp=self.started + timedelta(minutes=minutes),
        )

    def test_count_screen_offers_keep_or_throw_away(self):
        self.tap("bike", 3)
        response = self.client.get(
            reverse("counts_count", args=[self.session.pk])
        )
        self.assertContains(response, "wasn’t finished")
        self.assertContains(response, "Decide later")
        self.assertContains(response, "Keep what was counted")
        self.assertContains(response, "Throw it away")
        self.assertNotContains(response, "count-config")

    def test_stale_page_can_send_what_the_phone_saved(self):
        response = self.client.get(
            reverse("counts_count", args=[self.session.pk])
        )
        config = response.context["rescue"]
        self.assertEqual(config["sessionId"], self.session.pk)
        self.assertEqual(
            config["eventUrl"],
            reverse("counts_event", args=[self.session.pk]),
        )
        self.assertEqual(
            config["finishUrl"],
            reverse("counts_finish", args=[self.session.pk]),
        )
        self.assertContains(response, 'id="rescue-config"')
        self.assertContains(response, "count_rescue.js")

    def test_taps_right_at_the_deadline(self):
        deadline = count_deadline(self.session)
        # Taps carry milliseconds: the last one within the window.
        last = deadline - timedelta(milliseconds=1)
        self.assertEqual(self.send_tap(last).status_code, 204)
        self.assertEqual(
            self.send_tap(deadline + timedelta(seconds=1)).status_code, 409
        )

    def test_a_tap_without_a_time_is_judged_by_its_arrival(self):
        response = self.client.post(
            reverse("counts_event", args=[self.session.pk]),
            data={"mode": "bike", "event_id": str(uuid.uuid4())},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)

    def test_a_finish_just_after_the_deadline_ends_at_the_last_tap(self):
        self.tap("ped", 4)
        late = count_deadline(self.session) + timedelta(seconds=1)
        self.client.post(
            reverse("counts_finish", args=[self.session.pk]),
            data={
                "totals": {"ped": 1},
                "finished_at": late.timestamp() * 1000,
            },
            content_type="application/json",
        )
        self.session.refresh_from_db()
        self.assertEqual(
            self.session.finished_at, self.started + timedelta(minutes=4)
        )

    def test_old_taps_past_the_deadline_never_stretch_it(self):
        # Stored before the deadline was enforced.
        self.tap("ped", 4)
        self.tap("ped", 60 * 24)
        self.client.post(self.url, {"action": "keep"})
        self.session.refresh_from_db()
        self.assertEqual(
            self.session.finished_at, self.started + timedelta(minutes=4)
        )

    def test_nothing_to_keep_without_taps(self):
        response = self.client.get(
            reverse("counts_count", args=[self.session.pk])
        )
        self.assertNotContains(response, "Keep what was counted")

    def test_keep_ends_at_the_last_tap_with_the_taps_received(self):
        self.tap("bike", 3)
        self.tap("bike", 5)
        self.tap("car", 9)
        response = self.client.post(self.url, {"action": "keep"})
        self.session.refresh_from_db()
        self.assertRedirects(
            response, reverse("counts_detail", args=[self.session.pk])
        )
        self.assertEqual(
            self.session.finished_at, self.started + timedelta(minutes=9)
        )
        self.assertEqual(self.session.total_cyclist, 2)
        self.assertEqual(self.session.total_car, 1)
        self.assertEqual(
            self.session.publication_state, State.PENDING_MODERATION
        )
        self.assertEqual(
            self.session.integrity_hash,
            self.session.compute_integrity_hash(),
        )

    def test_keep_never_ends_before_the_start(self):
        # Tap times may be a little before the start (clock skew).
        self.tap("bike", -2)
        self.client.post(self.url, {"action": "keep"})
        self.session.refresh_from_db()
        self.assertEqual(self.session.finished_at, self.started)

    def test_keep_without_taps_saves_nothing(self):
        self.client.post(self.url, {"action": "keep"})
        self.session.refresh_from_db()
        self.assertIsNone(self.session.finished_at)

    def test_already_finished_goes_to_the_results(self):
        ModalShareSession.objects.filter(pk=self.session.pk).update(
            finished_at=self.started + timedelta(minutes=10)
        )
        for action in ("keep", "discard"):
            with self.subTest(action=action):
                response = self.client.post(self.url, {"action": action})
                self.assertRedirects(
                    response,
                    reverse("counts_detail", args=[self.session.pk]),
                )
                self.assertTrue(
                    ModalShareSession.objects.filter(
                        pk=self.session.pk
                    ).exists()
                )

    def test_someone_elses_is_not_found(self):
        other = MobilitoUser.objects.create_user("them@example.com")
        self.client.force_login(other)
        response = self.client.post(self.url, {"action": "discard"})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(ModalShareSession.objects.exists())

    def test_provisional_keep_waits_for_the_emailed_link(self):
        self.client.logout()
        self.client.post(
            reverse("auth_observe"), {"email": "walker@example.com"}
        )
        ModalShareSession.objects.filter(pk=self.session.pk).update(
            user=None, sign_in_attempt=SignInAttempt.objects.get()
        )
        self.tap("ped", 2)
        self.client.post(self.url, {"action": "keep"})
        self.session.refresh_from_db()
        self.assertEqual(
            self.session.publication_state, State.PENDING_VALIDATION
        )

    def test_throw_away(self):
        self.tap("bike", 3)
        response = self.client.post(self.url, {"action": "discard"})
        self.assertRedirects(response, reverse("my_observations"))
        self.assertFalse(ModalShareSession.objects.exists())

    def send_tap(self, when):
        return self.client.post(
            reverse("counts_event", args=[self.session.pk]),
            data={
                "mode": "bike",
                "event_id": str(uuid.uuid4()),
                "client_timestamp": when.timestamp() * 1000,
            },
            content_type="application/json",
        )

    def test_no_new_taps(self):
        response = self.send_tap(timezone.now())
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.session.events.exists())

    def test_taps_made_in_time_but_sent_late_still_count(self):
        # Counted offline; the phone found a signal days later.
        response = self.send_tap(self.started + timedelta(minutes=7))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.session.events.count(), 1)

    def test_a_finish_made_in_time_but_sent_late_keeps_its_time(self):
        self.tap("ped", 4)
        ended = self.started + timedelta(minutes=20)
        self.client.post(
            reverse("counts_finish", args=[self.session.pk]),
            data={
                "totals": {"ped": 1},
                "finished_at": ended.timestamp() * 1000,
            },
            content_type="application/json",
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.finished_at, ended)

    def test_finishing_without_taps_from_an_old_page_keeps_nothing(self):
        # As for close_stale: nothing counted in time, nothing to keep.
        response = self.client.post(
            reverse("counts_finish", args=[self.session.pk]),
            data={"totals": {"bike": 3}},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.session.refresh_from_db()
        self.assertIsNone(self.session.finished_at)

    def test_a_late_finish_counts_only_the_taps_in_time(self):
        self.tap("ped", 4)
        self.tap("ped", 60 * 24)  # stored before the deadline applied
        self.client.post(
            reverse("counts_finish", args=[self.session.pk]),
            # The phone ran on past the window: 5 in its own totals.
            data={"totals": {"ped": 5}},
            content_type="application/json",
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.total_pedestrian, 1)
        self.assertEqual(
            self.session.integrity_hash,
            self.session.compute_integrity_hash(),
        )

    def test_finishing_from_an_old_page_ends_at_the_last_tap(self):
        self.tap("ped", 4)
        self.client.post(
            reverse("counts_finish", args=[self.session.pk]),
            data={"totals": {"ped": 1}},
            content_type="application/json",
        )
        self.session.refresh_from_db()
        self.assertEqual(
            self.session.finished_at, self.started + timedelta(minutes=4)
        )

    def test_a_recent_count_is_left_alone(self):
        ModalShareSession.objects.filter(pk=self.session.pk).update(
            started_at=timezone.now()
        )
        response = self.client.post(self.url, {"action": "discard"})
        self.assertRedirects(
            response,
            reverse("counts_count", args=[self.session.pk]),
            fetch_redirect_response=False,
        )
        self.assertTrue(ModalShareSession.objects.exists())
