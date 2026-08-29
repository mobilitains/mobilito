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
from django.test import TestCase
from django.utils import timezone

from authentication.models import MobilitoUser
from core.models import Location, LocationEvidence
from mobilito_app.models import ModalShareSession


class LocationTests(TestCase):
    def test_str_prefers_user_entered_address(self):
        location = Location.objects.create(
            point=Point(-1.5536, 47.2184),
            user_entered_address="12 rue de la Paix",
            reverse_geocoded_address="Should not be used",
        )
        self.assertEqual(str(location), "12 rue de la Paix")

    def test_str_falls_back_to_reverse_geocoded_address(self):
        location = Location.objects.create(
            point=Point(-1.5536, 47.2184),
            reverse_geocoded_address="Rue de la Paix, Nantes",
        )
        self.assertEqual(str(location), "Rue de la Paix, Nantes")

    def test_str_falls_back_to_pk(self):
        location = Location.objects.create(point=Point(-1.5536, 47.2184))
        self.assertEqual(str(location), f"Location {location.pk}")


class LocationEvidenceTests(TestCase):
    def test_attaches_generically_to_an_observation(self):
        user = MobilitoUser.objects.create_user("evidence@example.com")
        location = Location.objects.create(point=Point(-1.5536, 47.2184))
        session = ModalShareSession.objects.create(
            user=user,
            location=location,
            started_at=timezone.now(),
        )
        evidence = LocationEvidence.objects.create(
            observation=session,
            device_point=Point(-1.5540, 47.2180),
            accuracy_metres=8.5,
            timestamp=timezone.now(),
        )
        self.assertEqual(evidence.observation, session)
        self.assertEqual(evidence.content_type.model, "modalsharesession")
