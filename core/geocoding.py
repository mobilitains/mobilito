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

# Reverse geocoding behind a swappable provider (roadmap Phase 4).
#
# Views call reverse_geocode(); which provider answers is a setting
# (GEOCODING_BACKEND), so moving between Nominatim and Mapbox, or
# adding another, never touches views.

import http.client
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeocodeResult:
    """A human-readable address plus area metadata (§11.1, §20.2)."""

    address: str = ""
    country: str = ""
    region: str = ""
    department: str = ""
    commune: str = ""


class GeocoderError(Exception):
    pass


def _fetch_json(url: str, timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            # Nominatim's usage policy requires an identifying agent.
            "User-Agent": settings.GEOCODING_USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except (
        urllib.error.URLError,
        http.client.HTTPException,
        OSError,
        ValueError,
    ) as err:
        raise GeocoderError(str(err)) from err
    if not isinstance(data, dict):
        raise GeocoderError(f"Unexpected response: {type(data).__name__}")
    return data


class NominatimGeocoder:
    """OpenStreetMap Nominatim (free; at most 1 request per second).

    The throttle and cache only span processes when the default
    cache is shared (see the core.W001 system check).
    """

    # Results may be stored (ODbL, with attribution).
    cacheable = True

    THROTTLE_KEY = "geocoding:nominatim:throttle"

    def _wait_for_slot(self) -> bool:
        """Claim the one-request-per-second slot, waiting briefly.

        Shares the slot across processes via the cache (when the
        cache is shared). Gives up rather than keep a user waiting.
        """
        deadline = time.monotonic() + settings.GEOCODING_MAX_WAIT_SECONDS
        while True:
            if cache.add(self.THROTTLE_KEY, 1, timeout=1):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)

    def reverse(self, lat: float, lon: float, language: str):
        if not self._wait_for_slot():
            raise GeocoderError("Nominatim throttle slot unavailable")
        query = urllib.parse.urlencode(
            {
                "format": "jsonv2",
                "lat": f"{lat:.6f}",
                "lon": f"{lon:.6f}",
                "zoom": 18,
                "addressdetails": 1,
                "accept-language": language,
            }
        )
        data = _fetch_json(
            f"{settings.NOMINATIM_URL}?{query}",
            settings.GEOCODING_TIMEOUT_SECONDS,
        )
        if "error" in data:
            return None
        return self.parse(data)

    @staticmethod
    def parse(data: dict) -> GeocodeResult:
        address = data.get("address", {})
        commune = next(
            (
                address[key]
                for key in ("city", "town", "village", "municipality")
                if address.get(key)
            ),
            "",
        )
        street = " ".join(
            part
            for part in (address.get("house_number"), address.get("road"))
            if part
        )
        # No street (a park, a square): fall back to the place's name.
        first = street or data.get("name") or ""
        return GeocodeResult(
            address=", ".join(part for part in (first, commune) if part),
            country=address.get("country", ""),
            region=address.get("state", ""),
            # In France Nominatim puts the département in "county".
            department=address.get("county", ""),
            commune=commune,
        )


class MapboxGeocoder:
    """Mapbox Geocoding API v6 (needs MAPBOX_ACCESS_TOKEN).

    Mapbox's terms forbid storing "temporary" results; storing them
    (in our cache, or later on a Location) needs permanent=true,
    which is a paid feature. MAPBOX_PERMANENT turns that on.
    """

    URL = "https://api.mapbox.com/search/geocode/v6/reverse"

    @property
    def cacheable(self) -> bool:
        return settings.MAPBOX_PERMANENT

    def reverse(self, lat: float, lon: float, language: str):
        if not settings.MAPBOX_ACCESS_TOKEN:
            raise GeocoderError("MAPBOX_ACCESS_TOKEN is not set")
        query = urllib.parse.urlencode(
            {
                "latitude": f"{lat:.6f}",
                "longitude": f"{lon:.6f}",
                "language": language,
                "limit": 1,
                "permanent": "true" if self.cacheable else "false",
                "access_token": settings.MAPBOX_ACCESS_TOKEN,
            }
        )
        data = _fetch_json(
            f"{self.URL}?{query}", settings.GEOCODING_TIMEOUT_SECONDS
        )
        features = data.get("features") or []
        if not features:
            return None
        return self.parse(features[0])

    @staticmethod
    def parse(feature: dict) -> GeocodeResult:
        properties = feature.get("properties", {})
        context = properties.get("context", {})

        def name(key: str) -> str:
            return (context.get(key) or {}).get("name", "")

        commune = name("place") or name("locality")
        first = properties.get("name", "")
        return GeocodeResult(
            address=", ".join(part for part in (first, commune) if part),
            country=name("country"),
            region=name("region"),
            department=name("district"),
            commune=commune,
        )


def get_geocoder():
    return import_string(settings.GEOCODING_BACKEND)()


def reverse_geocode(lat: float, lon: float, language: str):
    """Return a GeocodeResult for the point, or None if unavailable.

    Never raises for provider trouble: the address is only ever a
    suggestion the user can edit or type, so failure just means an
    empty field. Results are cached per ~1 m cell and language, which
    also keeps us inside free-tier quotas.
    """
    # Bump the version whenever GeocodeResult's fields change.
    key = f"geocoding:v1:reverse:{lat:.5f}:{lon:.5f}:{language}"
    cached = cache.get(key)
    if cached is not None:
        return GeocodeResult(**cached) if cached else None
    geocoder = get_geocoder()
    try:
        result = geocoder.reverse(lat, lon, language)
    except GeocoderError as err:
        logger.warning("Reverse geocoding failed: %s", err)
        return None
    if not geocoder.cacheable:
        return result
    cache.set(
        key,
        result.__dict__ if result else {},
        timeout=settings.GEOCODING_CACHE_SECONDS,
    )
    return result
