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

import hashlib

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from authentication.models import MobilitoUser
from core.middleware import SyncUserLanguageMiddleware
from core.models import Location, LocationEvidence
from core.ratelimit import client_ip, is_rate_limited
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


class SetLanguageViewTests(TestCase):
    def test_get_not_allowed(self):
        response = self.client.get(reverse("set_language"))
        self.assertEqual(response.status_code, 405)

    def test_anonymous_sets_language_cookie(self):
        response = self.client.post(
            reverse("set_language"), {"language": "en", "next": "/"}
        )
        self.assertRedirects(response, "/")
        self.assertEqual(
            response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "en"
        )

    def test_authenticated_user_stores_preference(self):
        user = MobilitoUser.objects.create_user("lang@example.com")
        self.client.force_login(user)
        self.client.post(
            reverse("set_language"), {"language": "en", "next": "/"}
        )
        user.refresh_from_db()
        self.assertEqual(user.preferred_language, "en")

    def test_invalid_language_is_ignored(self):
        response = self.client.post(
            reverse("set_language"), {"language": "xx", "next": "/"}
        )
        self.assertRedirects(response, "/")
        self.assertNotIn(settings.LANGUAGE_COOKIE_NAME, response.cookies)

    def test_unsafe_next_falls_back_to_home(self):
        response = self.client.post(
            reverse("set_language"),
            {"language": "en", "next": "https://evil.example/"},
        )
        self.assertRedirects(response, "/")

    def test_htmx_request_gets_hx_redirect_header(self):
        response = self.client.post(
            reverse("set_language"),
            {"language": "en", "next": "/"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Redirect"], "/")


class SyncUserLanguageMiddlewareTests(TestCase):
    def _run(self, request):
        seen_cookies = {}

        def get_response(inner_request):
            seen_cookies.update(inner_request.COOKIES)
            return HttpResponse()

        SyncUserLanguageMiddleware(get_response)(request)
        return seen_cookies

    def test_authenticated_users_preference_overrides_cookie(self):
        user = MobilitoUser.objects.create_user("sync@example.com")
        user.preferred_language = "en"
        user.save(update_fields=["preferred_language"])
        request = RequestFactory().get("/")
        request.user = user
        request.COOKIES[settings.LANGUAGE_COOKIE_NAME] = "fr"

        seen_cookies = self._run(request)

        self.assertEqual(seen_cookies[settings.LANGUAGE_COOKIE_NAME], "en")

    def test_anonymous_user_leaves_cookie_untouched(self):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        request.COOKIES[settings.LANGUAGE_COOKIE_NAME] = "fr"

        seen_cookies = self._run(request)

        self.assertEqual(seen_cookies[settings.LANGUAGE_COOKIE_NAME], "fr")


class RateLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_allows_up_to_limit_then_blocks(self):
        results = [is_rate_limited("t", "a", 3, 60) for _ in range(4)]
        self.assertEqual(results, [False, False, False, True])

    def test_identifiers_are_counted_separately(self):
        self.assertFalse(is_rate_limited("t", "a", 1, 60))
        self.assertFalse(is_rate_limited("t", "b", 1, 60))
        self.assertTrue(is_rate_limited("t", "a", 1, 60))

    def test_scopes_are_counted_separately(self):
        self.assertFalse(is_rate_limited("s1", "a", 1, 60))
        self.assertFalse(is_rate_limited("s2", "a", 1, 60))

    def test_identifier_not_stored_verbatim(self):
        is_rate_limited("t", "someone@example.com", 1, 60)
        self.assertIsNone(cache.get("ratelimit:t:someone@example.com"))
        digest = hashlib.sha256(b"someone@example.com").hexdigest()
        self.assertEqual(cache.get(f"ratelimit:t:{digest}"), 1)


class ClientIpTests(TestCase):
    def test_uses_remote_addr_by_default(self):
        request = RequestFactory().get(
            "/", REMOTE_ADDR="192.0.2.1", HTTP_CF_CONNECTING_IP="198.51.100.1"
        )
        self.assertEqual(client_ip(request), "192.0.2.1")

    @override_settings(RATE_LIMIT_CLIENT_IP_HEADER="HTTP_CF_CONNECTING_IP")
    def test_uses_trusted_header_when_configured(self):
        request = RequestFactory().get(
            "/",
            REMOTE_ADDR="192.0.2.1",
            HTTP_CF_CONNECTING_IP="198.51.100.1, 203.0.113.9",
        )
        self.assertEqual(client_ip(request), "198.51.100.1")

    @override_settings(RATE_LIMIT_CLIENT_IP_HEADER="HTTP_CF_CONNECTING_IP")
    def test_falls_back_when_header_missing(self):
        request = RequestFactory().get("/", REMOTE_ADDR="192.0.2.1")
        self.assertEqual(client_ip(request), "192.0.2.1")
