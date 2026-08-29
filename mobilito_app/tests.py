"""
Copyright 2024  Francais pour une Meilleure Mobilité.

This file is part of the mobilito web application.

Mobilito is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Mobilito is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with mobilito.  If not, see <http://www.gnu.org/licenses/>.
"""

from django.contrib.gis.geos import Point
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from authentication.models import MobilitoUser
from core.models import Location, ModerationState, PublicationState
from mobilito_app.models import (
    ActionType,
    ContactMethod,
    InfrastructureMedia,
    InfrastructureObservation,
    InfrastructureTag,
    Mode,
    ModalShareCountEvent,
    ModalShareSession,
    ModerationFlag,
    ObservationAction,
    ObserverPerspective,
)


def make_location() -> Location:
    return Location.objects.create(point=Point(-1.5536, 47.2184))


def make_user(email: str = "user@example.com") -> MobilitoUser:
    return MobilitoUser.objects.create_user(email)


class ModalShareSessionTests(TestCase):
    def test_defaults_to_draft(self):
        session = ModalShareSession.objects.create(
            user=make_user(),
            location=make_location(),
            started_at=timezone.now(),
        )
        self.assertEqual(session.publication_state, PublicationState.DRAFT)
        self.assertEqual(session.total_pedestrian, 0)
        self.assertFalse(session.location_mismatch)

    def test_str_includes_location(self):
        location = make_location()
        session = ModalShareSession.objects.create(
            user=make_user(),
            location=location,
            started_at=timezone.now(),
        )
        self.assertIn(str(location), str(session))


class ModalShareCountEventTests(TestCase):
    def test_events_are_recorded_per_session(self):
        session = ModalShareSession.objects.create(
            user=make_user(),
            location=make_location(),
            started_at=timezone.now(),
        )
        event = ModalShareCountEvent.objects.create(
            session=session,
            timestamp=timezone.now(),
            mode=Mode.CYCLIST,
        )
        self.assertEqual(session.events.count(), 1)
        self.assertEqual(event.mode, "bike")


class InfrastructureObservationTests(TestCase):
    def test_defaults(self):
        observation = InfrastructureObservation.objects.create(
            user=make_user(),
            location=make_location(),
            observer_perspective=ObserverPerspective.PEDESTRIAN,
        )
        self.assertEqual(observation.publication_state, PublicationState.DRAFT)
        self.assertEqual(
            observation.moderation_state, ModerationState.UNREVIEWED
        )

    def test_media_and_flag_attach_correctly(self):
        observation = InfrastructureObservation.objects.create(
            user=make_user(),
            location=make_location(),
            observer_perspective=ObserverPerspective.BOTH,
        )
        media = InfrastructureMedia.objects.create(
            observation=observation,
            added_by=observation.user,
            file="infrastructure_media/2026/08/example.jpg",
        )
        flag = ModerationFlag.objects.create(
            target=media,
            reporter=make_user("reporter@example.com"),
        )
        self.assertEqual(flag.target, media)
        self.assertEqual(observation.media.count(), 1)


class ObservationActionTests(TestCase):
    def test_me_too_creation(self):
        observation = InfrastructureObservation.objects.create(
            user=make_user(),
            location=make_location(),
            observer_perspective=ObserverPerspective.CYCLIST,
        )
        action = ObservationAction.objects.create(
            observation=observation,
            action_type=ActionType.ME_TOO,
            created_by=make_user("metoo@example.com"),
        )
        self.assertIsNone(action.cancelled_at)
        self.assertEqual(observation.actions.count(), 1)


class ContactMethodTests(TestCase):
    def test_str_uses_narrowest_zone(self):
        contact = ContactMethod.objects.create(
            country="FR",
            commune="Nantes",
            contact_value="voirie@nantesmetropole.fr",
        )
        self.assertIn("Nantes", str(contact))
        self.assertIn("voirie@nantesmetropole.fr", str(contact))


class InfrastructureTagFixtureTests(TestCase):
    def test_initial_fixture_loads_bilingual_tags(self):
        call_command("loaddata", "initial_infrastructure_tags")
        self.assertGreater(InfrastructureTag.objects.count(), 0)
        tag = InfrastructureTag.objects.get(pk=1)
        self.assertTrue(tag.label_fr)
        self.assertTrue(tag.label_en)
        self.assertNotEqual(tag.label_fr, tag.label_en)


class HomeViewTests(TestCase):
    def test_visitor_sees_landing_ctas(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "mobilito_app/home.html")
        self.assertContains(response, "Browse observations")
        self.assertContains(response, "Make an observation")
        self.assertNotContains(response, "Count modal share")

    def test_authenticated_user_sees_observation_buttons(self):
        user = make_user("home@example.com")
        self.client.force_login(user)
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Count modal share")
        self.assertContains(response, "Report an aménagement")
        self.assertNotContains(response, "Browse observations")

    def test_no_template_comments_leak_into_rendered_html(self):
        # Regression check: Django's {# #} comment tag doesn't span
        # multiple lines, so a multi-line comment written that way
        # renders as literal text instead of being stripped.
        # Templates here use {% comment %}/{% endcomment %} instead,
        # for headers and any other multi-line comment.
        response = self.client.get(reverse("home"))
        content = response.content.decode()
        self.assertNotIn("Copyright", content)
        self.assertNotIn("{#", content)
        self.assertNotIn("{%", content)

        self.client.force_login(make_user("nav-comments@example.com"))
        response = self.client.get(reverse("home"))
        content = response.content.decode()
        self.assertNotIn("Copyright", content)
        self.assertNotIn("{#", content)
        self.assertNotIn("{%", content)
