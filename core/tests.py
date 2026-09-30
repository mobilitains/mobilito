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
import http.client
import io
from fractions import Fraction
import json
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.gis.geos import Point
from django.template.loader import render_to_string
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from authentication.models import MobilitoUser
from core.images import process_photo
from core.geocoding import (
    GeocodeResult,
    GeocoderError,
    MapboxGeocoder,
    NominatimGeocoder,
    reverse_geocode,
)
from core.maps import map_widget_config
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

    def test_safe_next_is_kept(self):
        response = self.client.post(
            reverse("set_language"),
            {"language": "en", "next": "/somewhere/?page=2"},
        )
        self.assertRedirects(
            response, "/somewhere/?page=2", fetch_redirect_response=False
        )

    def test_missing_next_falls_back_to_home(self):
        response = self.client.post(
            reverse("set_language"), {"language": "en"}
        )
        self.assertRedirects(response, "/")

    def test_http_next_rejected_on_secure_request(self):
        response = self.client.post(
            reverse("set_language"),
            {"language": "en", "next": "http://testserver/"},
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")

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


NOMINATIM_NANTES = {
    "name": "",
    "address": {
        "house_number": "2",
        "road": "Rue de Strasbourg",
        "city": "Nantes",
        "county": "Loire-Atlantique",
        "state": "Pays de la Loire",
        "country": "France",
        "country_code": "fr",
    },
}

MAPBOX_NANTES = {
    "properties": {
        "name": "2 Rue de Strasbourg",
        "context": {
            "country": {"name": "France", "country_code": "FR"},
            "region": {"name": "Pays de la Loire"},
            "district": {"name": "Loire-Atlantique"},
            "place": {"name": "Nantes"},
        },
    }
}

EXPECTED_NANTES = GeocodeResult(
    address="2 Rue de Strasbourg, Nantes",
    country="FR",
    region="Pays de la Loire",
    department="Loire-Atlantique",
    commune="Nantes",
)


class NominatimGeocoderTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_parse_street_address(self):
        self.assertEqual(
            NominatimGeocoder.parse(NOMINATIM_NANTES), EXPECTED_NANTES
        )

    def test_parse_place_without_street_uses_its_name(self):
        result = NominatimGeocoder.parse(
            {
                "name": "Parc de Procé",
                "address": {"town": "Nantes", "country": "France"},
            }
        )
        self.assertEqual(result.address, "Parc de Procé, Nantes")

    @mock.patch("core.geocoding.urllib.request.urlopen")
    def test_reverse_sends_policy_headers_and_language(self, urlopen):
        urlopen.return_value.__enter__.return_value = io.StringIO(
            json.dumps(NOMINATIM_NANTES)
        )
        result = NominatimGeocoder().reverse(47.2184, -1.5536, "en")
        self.assertEqual(result, EXPECTED_NANTES)
        request = urlopen.call_args[0][0]
        self.assertIn("accept-language=en", request.full_url)
        self.assertIn("lat=47.218400", request.full_url)
        self.assertTrue(request.get_header("User-agent"))

    @mock.patch("core.geocoding.urllib.request.urlopen")
    def test_nominatim_error_payload_means_no_result(self, urlopen):
        urlopen.return_value.__enter__.return_value = io.StringIO(
            json.dumps({"error": "Unable to geocode"})
        )
        self.assertIsNone(NominatimGeocoder().reverse(0, 0, "fr"))

    @mock.patch("core.geocoding.urllib.request.urlopen")
    def test_non_object_response_is_an_error(self, urlopen):
        urlopen.return_value.__enter__.return_value = io.StringIO("[]")
        with self.assertRaises(GeocoderError):
            NominatimGeocoder().reverse(0, 0, "fr")

    @mock.patch("core.geocoding.urllib.request.urlopen")
    def test_truncated_response_is_an_error(self, urlopen):
        urlopen.side_effect = http.client.IncompleteRead(b"")
        with self.assertRaises(GeocoderError):
            NominatimGeocoder().reverse(0, 0, "fr")

    @mock.patch("core.geocoding.urllib.request.urlopen")
    def test_each_request_claims_the_shared_slot(self, urlopen):
        urlopen.return_value.__enter__.return_value = io.StringIO(
            json.dumps(NOMINATIM_NANTES)
        )
        NominatimGeocoder().reverse(47.2, -1.5, "fr")
        self.assertIsNotNone(cache.get(NominatimGeocoder.THROTTLE_KEY))

    @override_settings(GEOCODING_MAX_WAIT_SECONDS=0)
    def test_throttled_when_slot_taken(self):
        cache.add(NominatimGeocoder.THROTTLE_KEY, 1, timeout=1)
        with self.assertRaises(GeocoderError):
            NominatimGeocoder().reverse(47.2, -1.5, "fr")


class MapboxGeocoderTests(TestCase):
    def test_parse(self):
        self.assertEqual(MapboxGeocoder.parse(MAPBOX_NANTES), EXPECTED_NANTES)

    @override_settings(MAPBOX_ACCESS_TOKEN="")
    def test_requires_token(self):
        with self.assertRaises(GeocoderError):
            MapboxGeocoder().reverse(47.2, -1.5, "fr")


class ReverseGeocodeTests(TestCase):
    def setUp(self):
        cache.clear()

    @mock.patch("core.geocoding.get_geocoder")
    def test_caches_results(self, get_geocoder):
        get_geocoder.return_value.reverse.return_value = EXPECTED_NANTES
        self.assertEqual(reverse_geocode(47.2, -1.5, "fr"), EXPECTED_NANTES)
        self.assertEqual(reverse_geocode(47.2, -1.5, "fr"), EXPECTED_NANTES)
        self.assertEqual(get_geocoder.return_value.reverse.call_count, 1)

    @mock.patch("core.geocoding.get_geocoder")
    def test_caches_misses_too(self, get_geocoder):
        get_geocoder.return_value.reverse.return_value = None
        self.assertIsNone(reverse_geocode(47.2, -1.5, "fr"))
        self.assertIsNone(reverse_geocode(47.2, -1.5, "fr"))
        self.assertEqual(get_geocoder.return_value.reverse.call_count, 1)

    @mock.patch("core.geocoding.get_geocoder")
    def test_provider_failure_returns_none_uncached(self, get_geocoder):
        get_geocoder.return_value.reverse.side_effect = GeocoderError("x")
        self.assertIsNone(reverse_geocode(47.2, -1.5, "fr"))
        self.assertIsNone(reverse_geocode(47.2, -1.5, "fr"))
        self.assertEqual(get_geocoder.return_value.reverse.call_count, 2)

    @override_settings(
        GEOCODING_BACKEND="core.geocoding.MapboxGeocoder",
        MAPBOX_PERMANENT=False,
    )
    @mock.patch.object(MapboxGeocoder, "reverse")
    def test_temporary_mapbox_results_are_not_cached(self, reverse):
        reverse.return_value = EXPECTED_NANTES
        reverse_geocode(47.3, -1.5, "fr")
        reverse_geocode(47.3, -1.5, "fr")
        self.assertEqual(reverse.call_count, 2)

    @override_settings(GEOCODING_BACKEND="core.geocoding.MapboxGeocoder")
    @mock.patch.object(MapboxGeocoder, "reverse")
    def test_backend_is_a_setting(self, reverse):
        reverse.return_value = EXPECTED_NANTES
        self.assertEqual(reverse_geocode(47.3, -1.5, "fr"), EXPECTED_NANTES)
        reverse.assert_called_once()


class LocationConfirmViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = MobilitoUser.objects.create_user("map@example.com")
        self.client.force_login(self.user)

    def test_requires_sign_in(self):
        self.client.logout()
        response = self.client.post(
            reverse("location_confirm"),
            {"map-lat": "47.2", "map-lon": "-1.5"},
        )
        self.assertEqual(response.status_code, 302)

    @mock.patch("core.views.reverse_geocode", return_value=EXPECTED_NANTES)
    def test_returns_editable_address_and_coordinates(self, geocode):
        response = self.client.post(
            reverse("location_confirm"),
            {
                "widget": "m2",
                "map-lat": "47.2184",
                "map-lon": "-1.5536",
                "map-device_lat": "47.2185",
                "map-device_lon": "-1.5537",
                "map-device_accuracy": "8",
            },
        )
        self.assertContains(response, 'value="2 Rue de Strasbourg, Nantes"')
        self.assertContains(response, "Location confirmed.")
        self.assertContains(response, 'id="m2-address"')
        self.assertContains(response, 'name="lat" value="47.218400"')
        self.assertContains(response, 'name="device_lat" value="47.218500"')
        self.assertContains(response, 'name="device_accuracy" value="8.0"')
        self.assertNotContains(response, "<html")
        # In the request's language (tests run in English).
        geocode.assert_called_once_with(47.2184, -1.5536, "en")

    @mock.patch("core.views.reverse_geocode", return_value=None)
    def test_no_address_invites_a_name(self, geocode):
        response = self.client.post(
            reverse("location_confirm"),
            {"map-lat": "47.2", "map-lon": "-1.5"},
        )
        self.assertContains(response, "No address suggestion available")
        self.assertNotContains(response, 'name="device_lat"')

    def test_out_of_range_position_is_rejected(self):
        response = self.client.post(
            reverse("location_confirm"), {"map-lat": "95", "map-lon": "-1.5"}
        )
        # 200, so that htmx 2 swaps the message in.
        self.assertContains(response, "couldn't read that position")
        self.assertNotContains(response, 'name="lat"')

    def test_missing_position_is_rejected(self):
        response = self.client.post(reverse("location_confirm"), {})
        self.assertContains(response, "couldn't read that position")

    def test_provisional_observer_can_confirm(self):
        self.client.logout()
        self.client.post(
            reverse("auth_observe"), {"email": "walker@example.com"}
        )
        with mock.patch("core.views.reverse_geocode", return_value=None):
            response = self.client.post(
                reverse("location_confirm"),
                {"map-lat": "47.2", "map-lon": "-1.5"},
            )
        self.assertContains(response, "Location confirmed.")

    def test_htmx_request_from_nobody_asks_for_an_email(self):
        self.client.logout()
        response = self.client.post(
            reverse("location_confirm"),
            {"map-lat": "47.2", "map-lon": "-1.5"},
            HTTP_HX_REQUEST="true",
            HTTP_HX_CURRENT_URL="http://testserver/reports/new/",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["HX-Redirect"],
            reverse("auth_observe") + "?next=%2Freports%2Fnew%2F",
        )

    @mock.patch("core.views.reverse_geocode", return_value=None)
    def test_widget_id_scopes_element_ids(self, geocode):
        response = self.client.post(
            reverse("location_confirm"),
            {"widget": "m2", "map-lat": "47.2", "map-lon": "-1.5"},
        )
        self.assertContains(response, 'id="m2-address"')

    @mock.patch("core.views.reverse_geocode", return_value=None)
    def test_bad_widget_id_falls_back(self, geocode):
        response = self.client.post(
            reverse("location_confirm"),
            {"widget": '"><script>', "map-lat": "47.2", "map-lon": "-1.5"},
        )
        self.assertContains(response, 'id="map-address"')
        self.assertNotContains(response, "<script>")

    @override_settings(RATE_LIMIT_LOCATION_CONFIRM=(1, 60))
    @mock.patch("core.views.reverse_geocode", return_value=EXPECTED_NANTES)
    def test_rate_limited_requests_skip_geocoding(self, geocode):
        for _ in range(2):
            response = self.client.post(
                reverse("location_confirm"),
                {"map-lat": "47.2", "map-lon": "-1.5"},
            )
        self.assertEqual(geocode.call_count, 1)
        self.assertContains(response, 'name="lat"')


class DeviceLocationPreferenceTests(TestCase):
    def test_signed_in_preference_is_stored_on_user(self):
        user = MobilitoUser.objects.create_user("pref@example.com")
        self.client.force_login(user)
        response = self.client.post(reverse("set_device_location"), {})
        self.assertEqual(response.status_code, 204)
        user.refresh_from_db()
        self.assertFalse(user.use_device_location)
        self.client.post(
            reverse("set_device_location"), {"use_device_location": "on"}
        )
        user.refresh_from_db()
        self.assertTrue(user.use_device_location)

    def test_anonymous_preference_is_stored_in_session(self):
        self.client.post(reverse("set_device_location"), {})
        self.assertIs(self.client.session["use_device_location"], False)
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        request.session = self.client.session
        self.assertFalse(map_widget_config(request)["use_device_location"])

    def test_get_not_allowed(self):
        response = self.client.get(reverse("set_device_location"))
        self.assertEqual(response.status_code, 405)


class MapWidgetTests(TestCase):
    def _request(self, user=None):
        request = RequestFactory().get("/")
        request.user = user or AnonymousUser()
        request.session = self.client.session
        return request

    def _render(self, **options):
        request = self._request(options.pop("user", None))
        config = map_widget_config(request, **options)
        return render_to_string(
            "core/includes/map_widget.html",
            {"map": config},
            request=request,
        )

    def test_defaults(self):
        config = map_widget_config(self._request())
        self.assertEqual(
            config["js"]["center"], list(settings.MAP_DEFAULT_CENTER)
        )
        self.assertEqual(config["js"]["zoom"], settings.MAP_DEFAULT_ZOOM)
        self.assertTrue(config["use_device_location"])

    def test_user_preference_is_used(self):
        user = MobilitoUser.objects.create_user("nogps@example.com")
        user.use_device_location = False
        user.save()
        config = map_widget_config(self._request(user))
        self.assertFalse(config["js"]["useDeviceLocation"])

    def test_config_is_emitted_as_json(self):
        html = self._render(widget_id="m1", pins_url="/api/x")
        self.assertIn('id="m1-config"', html)
        self.assertIn('"pinsUrl": "/api/x"', html)

    def test_crosshair_mode_renders_confirm_button(self):
        html = self._render(crosshair=True)
        self.assertIn("mobilito-crosshair", html)
        self.assertIn(f'hx-post="{reverse("location_confirm")}"', html)
        for name in ("lat", "lon", "device_lat", "device_lon"):
            self.assertIn(f'name="map-{name}" data-map-field="{name}"', html)

    def test_rejects_unsafe_widget_id(self):
        with self.assertRaises(ValueError):
            map_widget_config(self._request(), widget_id='x"y')

    def test_widget_controls_limit_what_they_send(self):
        # An enclosing observation form's fields (photos!) mustn't
        # ride along with the widget's own requests.
        html = self._render(crosshair=True, gps=True)
        self.assertIn('hx-params="use_device_location"', html)
        self.assertIn('hx-params="widget,map-lat,map-lon,', html)

    def test_map_dependent_controls_start_hidden(self):
        # Revealed by the JS only once the map exists.
        html = self._render(crosshair=True, gps=True)
        self.assertIn("data-map-confirm data-map-requires-js hidden", html)
        self.assertIn("data-map-requires-js hidden>\n      <input", html)

    def test_widget_contains_no_forms(self):
        # It must be able to sit inside an observation's own form.
        html = self._render(crosshair=True, gps=True, pins_url="/x")
        self.assertNotIn("<form", html)

    def test_plain_map_has_no_crosshair_or_gps(self):
        html = self._render()
        self.assertNotIn("mobilito-crosshair", html)
        self.assertNotIn("data-map-gps", html)
        self.assertNotIn("data-map-field", html)

    def test_gps_mode_renders_button_and_preference(self):
        html = self._render(gps=True)
        self.assertIn("data-map-gps", html)
        self.assertIn("data-map-device-location", html)
        self.assertIn(f'hx-post="{reverse("set_device_location")}"', html)
        self.assertIn("location not checked", html)

    def test_no_template_comments_leak(self):
        html = self._render(crosshair=True, gps=True)
        self.assertNotIn("Copyright", html)
        self.assertNotIn("{%", html)


class SharedCacheCheckTests(TestCase):
    def test_warns_about_per_process_cache(self):
        from core.checks import shared_cache_check

        self.assertEqual(shared_cache_check(None)[0].id, "core.W001")

    @override_settings(
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.db.DatabaseCache",
                "LOCATION": "cache",
            }
        }
    )
    def test_shared_cache_passes(self):
        from core.checks import shared_cache_check

        self.assertEqual(shared_cache_check(None), [])


class CreditsTests(TestCase):
    def test_footer_credits_openstreetmap_for_addresses(self):
        response = self.client.get(reverse("home"))
        self.assertContains(
            response, 'href="https://www.openstreetmap.org/copyright"'
        )
        self.assertContains(response, "OpenStreetMap contributors")
        self.assertContains(response, f'href="{reverse("credits")}"')
        self.assertContains(response, f'href="{settings.SOURCE_CODE_URL}"')

    @override_settings(GEOCODING_BACKEND="core.geocoding.MapboxGeocoder")
    def test_footer_follows_geocoding_backend(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, "© Mapbox © OpenStreetMap")

    def test_credits_page(self):
        response = self.client.get(reverse("credits"))
        # The tile attribution is rendered as HTML, not escaped.
        self.assertContains(
            response,
            '<li>Map: &copy; <a href="https://www.openstreetmap.org/'
            'copyright">OpenStreetMap</a> contributors</li>',
            html=True,
        )
        self.assertContains(response, "Open Database License")
        # Credit for the proof of concept, without his email address.
        self.assertContains(response, "Benjamin Mourgues")
        self.assertNotContains(response, "@hotmail")
        self.assertContains(response, "Français pour une Meilleure Mobilité")
        self.assertContains(response, f'href="{settings.SOURCE_CODE_URL}"')

    def test_credits_page_in_french(self):
        response = self.client.get(
            reverse("credits"), HTTP_ACCEPT_LANGUAGE="fr"
        )
        self.assertContains(response, '<h1 class="h3 mb-3">Crédits</h1>')
        # The map's attribution is translated too.
        self.assertContains(
            response,
            'les contributeurs d’<a href="https://www.openstreetmap.org/'
            'copyright">OpenStreetMap</a>',
        )

    def test_not_found_page_has_footer(self):
        response = self.client.get("/no-such-page/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "<footer", status_code=404)

    @override_settings(GEOCODING_BACKEND="core.geocoding.NoSuchGeocoder")
    def test_check_rejects_unloadable_backend(self):
        from core.checks import geocoding_backend_check

        errors = geocoding_backend_check(None)
        self.assertEqual([e.id for e in errors], ["core.E002"])

    @override_settings(GEOCODING_BACKEND="core.geocoding.GeocodeResult")
    def test_check_rejects_backend_without_credit(self):
        from core.checks import geocoding_backend_check

        errors = geocoding_backend_check(None)
        self.assertEqual([e.id for e in errors], ["core.E003"])


def _hex_colours(path):
    import re

    with open(path, encoding="utf-8") as f:
        return re.findall(
            r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b", f.read()
        )


def _css_tokens(theme="light"):
    """The --mbl-* colours for a theme, read from mobilitains.css.

    Light: the :root block. Dark: the same, overridden by the
    [data-bs-theme="dark"] block. var() aliases are resolved.
    """
    import re

    path = settings.BASE_DIR / "core/static/core/css/mobilitains.css"
    with open(path, encoding="utf-8") as f:
        text = f.read()

    def block(selector):
        start = text.index(selector + " {")
        end = text.index("\n}", start)
        return text[start:end]

    blocks = [block(":root")]
    if theme == "dark":
        blocks.append(block('[data-bs-theme="dark"]'))
    tokens = {}
    for body in blocks:
        tokens.update(
            re.findall(r"--(mbl-[a-z0-9-]+):\s*(#[0-9a-f]{6});", body)
        )
        for name, target in re.findall(
            r"--(mbl-[a-z0-9-]+):\s*var\(--(mbl-[a-z0-9-]+)\);", body
        ):
            tokens[name] = tokens[target]
    return tokens


def _contrast(a, b):
    """WCAG 2 contrast ratio between two #rrggbb colours."""

    def luminance(colour):
        channels = [c / 255 for c in bytes.fromhex(colour[1:])]
        r, g, b = [
            c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
            for c in channels
        ]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class BrandColourTests(TestCase):
    """doc/colours.md: brand tokens only, and contrast first."""

    def test_header_has_logo_and_brand_stylesheet(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, "core/css/mobilitains.css")
        self.assertContains(response, 'alt="Les Mobilitains"', count=2)
        self.assertContains(response, "mbl-navbar")

    def test_no_raw_colours_outside_the_tokens(self):
        # Each exception carries a comment naming its token.
        allowed = {
            "core/static/core/css/mobilitains.css",
            "core/static/core/js/map_widget.js",
            "authentication/templates/authentication/email/" "magic_link.html",
            "templates/500.html",
        }
        white = {"#fff", "#ffffff"}
        offenders = {}
        for app in ("core", "mobilito_app", "authentication", "templates"):
            for path in (settings.BASE_DIR / app).rglob("*"):
                relative = path.relative_to(settings.BASE_DIR).as_posix()
                if (
                    path.suffix not in (".css", ".js", ".html")
                    or "/tests/" in relative
                    or relative in allowed
                ):
                    continue
                found = [
                    c for c in _hex_colours(path) if c.lower() not in white
                ]
                if found:
                    offenders[relative] = found
        self.assertEqual(offenders, {})

    def test_pairs_we_rely_on_meet_wcag_aa(self):
        tokens = _css_tokens()
        tokens["white"] = "#ffffff"
        text = [  # 4.5:1
            ("mbl-fonce", "white"),
            ("mbl-marine", "white"),
            ("mbl-marron", "white"),
            ("mbl-marron", "mbl-gris-light"),
            ("white", "mbl-marine"),
            ("white", "mbl-marine-dark"),
            ("mbl-fonce-dark", "mbl-bleu"),
            ("mbl-fonce", "mbl-bleu"),
            ("mbl-fonce-dark", "mbl-bleu-light"),
            ("mbl-fonce-dark", "mbl-gris"),
            ("white", "mbl-orange-dark"),
            ("white", "mbl-orange-darker"),
            ("mbl-orange-dark", "white"),
            ("white", "mbl-marine-light"),  # disabled navy button
            # Muted and error text inside tinted boxes (.alert).
            ("mbl-marron-dark", "mbl-bleu-tint"),
            ("mbl-marron-dark", "mbl-orange-tint"),
            ("mbl-marron-dark", "mbl-gris"),
            ("mbl-orange-darker", "mbl-orange-tint"),
        ]
        ui = [  # 3:1: edges, icons, pins
            ("mbl-bleu-gris", "white"),
            ("mbl-bleu-text", "white"),
            ("mbl-kind-count", "white"),
            ("mbl-kind-report", "white"),
        ]
        failures = [
            (fg, bg, round(_contrast(tokens[fg], tokens[bg]), 2))
            for pairs, floor in ((text, 4.5), (ui, 3.0))
            for fg, bg in pairs
            if _contrast(tokens[fg], tokens[bg]) < floor
        ]
        self.assertEqual(failures, [])

    def test_dark_pairs_meet_wcag_aa(self):
        tokens = _css_tokens("dark")
        tokens["white"] = "#ffffff"
        # Raised: hover, subtle boxes, light buttons. Pressed: rows.
        page, card = "mbl-page-dark", "mbl-surface-dark"
        pressed = "mbl-surface-dark-2"
        text = [  # 4.5:1
            ("mbl-gris", page),
            ("mbl-gris", card),
            ("mbl-bleu", page),  # headings
            ("mbl-bleu-light", page),  # links
            ("mbl-bleu-light", card),
            ("mbl-marron-text-dark", page),
            ("mbl-marron-text-dark", card),
            ("mbl-marron-text-dark", "mbl-bleu-tint-dark"),
            ("mbl-marron-text-dark", "mbl-orange-tint-dark"),
            ("mbl-orange-text-dark", page),
            ("mbl-orange-text-dark", card),
            ("mbl-orange-text-dark", "mbl-orange-tint-dark"),
            ("mbl-gris", "mbl-bleu-tint-dark"),
            ("mbl-gris", "mbl-orange-tint-dark"),
            ("white", "mbl-marine"),  # navy buttons
            ("white", "mbl-marine-light"),
            ("mbl-fonce-dark", "mbl-bleu"),  # contribute
            ("mbl-fonce-dark", "mbl-bleu-light"),  # outline hover
            ("white", "mbl-orange-dark"),  # danger
            ("mbl-gris", pressed),
            ("mbl-marron-text-dark-2", pressed),
        ]
        ui = [  # 3:1
            ("mbl-bleu-gris", page),  # field edges, navy buttons
            ("mbl-bleu-light", page),  # outline buttons
            ("mbl-kind-count-text", page),
            ("mbl-kind-count-text", card),
            ("mbl-kind-count-text", pressed),
            ("mbl-kind-report-text", page),
            ("mbl-kind-report-text", card),
            ("mbl-kind-report-text", pressed),
            ("mbl-check-dark", page),
            ("white", "mbl-check-dark"),  # the tick
        ]
        failures = [
            (fg, bg, round(_contrast(tokens[fg], tokens[bg]), 2))
            for pairs, floor in ((text, 4.5), (ui, 3.0))
            for fg, bg in pairs
            if _contrast(tokens[fg], tokens[bg]) < floor
        ]
        self.assertEqual(failures, [])

    def test_literal_colours_match_their_tokens(self):
        # Where a value has to be written out (see the exceptions
        # above), it must still be a brand token's value.
        values = {v.lower() for v in _css_tokens().values()}
        values |= {"#fff", "#ffffff"}
        for relative in (
            "core/static/core/js/map_widget.js",
            "authentication/templates/authentication/email/" "magic_link.html",
            "templates/500.html",
        ):
            found = {
                c.lower() for c in _hex_colours(settings.BASE_DIR / relative)
            }
            self.assertLessEqual(found, values, relative)

    def test_map_pins_match_the_legend(self):
        import re

        tokens = _css_tokens()
        path = settings.BASE_DIR / "core/static/core/js/map_widget.js"
        with open(path, encoding="utf-8") as f:
            js = f.read()
        pins = dict(re.findall(r"(count|report): '(#[0-9a-f]{6})'", js))
        self.assertEqual(pins["count"], tokens["mbl-kind-count"])
        self.assertEqual(pins["report"], tokens["mbl-kind-report"])


class ThemeTests(TestCase):
    """Dark mode: opt-in, per device (doc/colours.md)."""

    def test_light_by_default(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'data-bs-theme="light"')
        self.assertContains(response, 'name="theme" value="dark"')
        self.assertContains(response, 'aria-checked="false"')

    def test_switching_to_dark_sets_a_cookie_and_goes_back(self):
        response = self.client.post(
            reverse("set_theme"), {"theme": "dark", "next": "/map/"}
        )
        self.assertRedirects(response, "/map/", fetch_redirect_response=False)
        cookie = response.cookies[settings.THEME_COOKIE_NAME]
        self.assertEqual(cookie.value, "dark")
        self.assertTrue(cookie["httponly"])
        page = self.client.get(reverse("home"))
        self.assertContains(page, 'data-bs-theme="dark"')
        self.assertContains(page, 'name="theme" value="light"')
        self.assertContains(page, 'aria-checked="true"')

    def test_switching_back_to_light(self):
        self.client.cookies[settings.THEME_COOKIE_NAME] = "dark"
        self.client.post(reverse("set_theme"), {"theme": "light"})
        page = self.client.get(reverse("home"))
        self.assertContains(page, 'data-bs-theme="light"')

    def test_unknown_theme_and_foreign_next_are_ignored(self):
        response = self.client.post(
            reverse("set_theme"),
            {"theme": "neon", "next": "https://evil.example/"},
        )
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertNotIn(settings.THEME_COOKIE_NAME, response.cookies)

    def test_a_forged_cookie_value_falls_back_to_light(self):
        self.client.cookies[settings.THEME_COOKIE_NAME] = '"><script>'
        page = self.client.get(reverse("home"))
        self.assertContains(page, 'data-bs-theme="light"')

    def test_htmx_gets_a_client_redirect(self):
        response = self.client.post(
            reverse("set_theme"),
            {"theme": "dark", "next": "/map/"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response["HX-Redirect"], "/map/")

    def test_get_is_refused(self):
        self.assertEqual(
            self.client.get(reverse("set_theme")).status_code, 405
        )


class BaseTemplateCsrfTests(TestCase):
    def test_htmx_requests_carry_csrf_header(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'hx-headers=\'{"X-CSRFToken": "')


def make_photo(
    fmt="JPEG",
    size=(40, 20),
    mode="RGB",
    gps=None,
    orientation=None,
    name="photo.jpg",
    refs=None,
    **save_options,
):
    """An in-memory upload; gps is (lat, lon), orientation an EXIF value."""
    from PIL import Image

    image = Image.new(mode, size, "red" if mode == "RGB" else None)
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    if gps:
        lat, lon = gps

        def dms(value):
            value = abs(value)
            degrees = int(value)
            minutes = int((value - degrees) * 60)
            seconds = (value - degrees - minutes / 60) * 3600
            return (
                Fraction(degrees),
                Fraction(minutes),
                Fraction(seconds).limit_denominator(10000),
            )

        lat_ref, lon_ref = refs or (
            "N" if lat >= 0 else "S",
            "E" if lon >= 0 else "W",
        )
        exif[0x8825] = {1: lat_ref, 2: dms(lat), 3: lon_ref, 4: dms(lon)}
    buffer = io.BytesIO()
    if fmt == "JPEG":
        image.save(buffer, fmt, exif=exif, **save_options)
    else:
        image.save(buffer, fmt, **save_options)
    return SimpleUploadedFile(name, buffer.getvalue())


class ProcessPhotoTests(TestCase):
    def open_result(self, processed):
        from PIL import Image

        return Image.open(io.BytesIO(processed.content.read()))

    def test_reencodes_as_jpeg_under_a_random_name(self):
        processed = process_photo(make_photo(name="my holiday.jpg"))
        self.assertTrue(processed.content.name.endswith(".jpg"))
        self.assertNotIn("holiday", processed.content.name)
        self.assertEqual(self.open_result(processed).format, "JPEG")

    def test_reads_gps_and_strips_all_metadata(self):
        from PIL import ImageCms

        icc = ImageCms.ImageCmsProfile(
            ImageCms.createProfile("sRGB")
        ).tobytes()
        processed = process_photo(
            make_photo(
                gps=(47.2184, -1.5536),
                comment=b"SECRET COMMENT",
                xmp=b"<x:xmpmeta>SECRET XMP</x:xmpmeta>",
                icc_profile=icc,
            )
        )
        self.assertAlmostEqual(processed.exif_point.y, 47.2184, places=3)
        self.assertAlmostEqual(processed.exif_point.x, -1.5536, places=3)
        raw = processed.content.read()
        self.assertNotIn(b"SECRET", raw)
        processed.content.seek(0)
        result = self.open_result(processed)
        self.assertEqual(len(result.getexif()), 0)
        self.assertEqual(
            {key for key in result.info if not key.startswith("jfif")},
            set(),
        )

    def test_southern_and_western_positions(self):
        processed = process_photo(make_photo(gps=(-33.9, -70.6)))
        self.assertLess(processed.exif_point.y, 0)
        self.assertLess(processed.exif_point.x, 0)

    def test_hemisphere_refs_as_bytes_or_lower_case(self):
        processed = process_photo(
            make_photo(gps=(33.9, 70.6), refs=(b"S\x00", "w"))
        )
        self.assertLess(processed.exif_point.y, 0)
        self.assertLess(processed.exif_point.x, 0)

    def test_out_of_range_gps_components_are_ignored(self):
        from PIL import Image

        image = Image.new("RGB", (10, 10))
        exif = Image.Exif()
        exif[0x8825] = {
            1: "N",
            2: (Fraction(47), Fraction(99), Fraction(0)),
            3: "E",
            4: (Fraction(1), Fraction(0), Fraction(0)),
        }
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", exif=exif)
        processed = process_photo(
            SimpleUploadedFile("x.jpg", buffer.getvalue())
        )
        self.assertIsNone(processed.exif_point)

    def test_no_gps_is_fine(self):
        self.assertIsNone(process_photo(make_photo()).exif_point)

    def test_applies_orientation_before_dropping_it(self):
        # Orientation 6 = rotate 90°: a 40x20 photo is shown 20x40.
        processed = process_photo(make_photo(orientation=6))
        self.assertEqual(self.open_result(processed).size, (20, 40))

    @override_settings(PHOTO_MAX_DIMENSION=10)
    def test_scales_down_keeping_proportions(self):
        processed = process_photo(make_photo(size=(40, 20)))
        self.assertEqual(self.open_result(processed).size, (10, 5))

    def test_png_with_transparency_becomes_jpeg_on_white(self):
        processed = process_photo(
            make_photo(fmt="PNG", mode="RGBA", name="shot.png")
        )
        result = self.open_result(processed)
        self.assertEqual(result.mode, "RGB")
        red, green, blue = result.getpixel((5, 5))
        self.assertGreater(min(red, green, blue), 240)

    @override_settings(PHOTO_MAX_PIXELS={"JPEG": 10**9, "PNG": 100})
    def test_caps_are_per_format(self):
        process_photo(make_photo(size=(40, 20)))  # JPEG: fine
        with self.assertRaises(ValidationError):
            process_photo(make_photo(fmt="PNG", size=(40, 20), name="x.png"))

    def test_rejections_are_logged(self):
        with self.assertLogs("core.images", "WARNING"):
            with self.assertRaises(ValidationError):
                process_photo(SimpleUploadedFile("x.jpg", b"not an image"))

    def test_refuses_formats_outside_the_allowlist(self):
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buffer, "BMP")
        with self.assertRaises(ValidationError):
            process_photo(SimpleUploadedFile("x.bmp", buffer.getvalue()))

    @override_settings(PHOTO_MAX_PIXELS={"JPEG": 100})
    def test_refuses_huge_images_before_decoding(self):
        with mock.patch("PIL.ImageFile.ImageFile.load") as load:
            with self.assertRaises(ValidationError):
                process_photo(make_photo(size=(40, 20)))
        load.assert_not_called()

    def test_refuses_what_is_not_an_image(self):
        with self.assertRaises(ValidationError) as caught:
            process_photo(SimpleUploadedFile("notes.jpg", b"not an image"))
        self.assertIn("notes.jpg", caught.exception.messages[0])

    @override_settings(PHOTO_MAX_UPLOAD_BYTES=10)
    def test_refuses_oversized_uploads(self):
        with self.assertRaises(ValidationError):
            process_photo(make_photo())

    def test_refuses_decompression_bombs(self):
        from PIL import Image

        with mock.patch.object(Image, "MAX_IMAGE_PIXELS", 100):
            with self.assertRaises(ValidationError):
                process_photo(make_photo(size=(400, 400)))
