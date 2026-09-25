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

# Server side of the reusable map component (roadmap Phase 4).
#
# Views build a config with map_widget_config() and pass it to the
# core/includes/map_widget.html include as `map`.

import re

from django.conf import settings
from django.urls import reverse

# Widget ids end up in element ids, CSS selectors and JSON attributes.
WIDGET_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,40}")

DEVICE_LOCATION_SESSION_KEY = "use_device_location"


def uses_device_location(request) -> bool:
    """The §11.2 "Use device location" preference, default on.

    Stored on the user when signed in (so it follows them across
    devices), otherwise in the session.
    """
    if request.user.is_authenticated:
        return request.user.use_device_location
    return request.session.get(DEVICE_LOCATION_SESSION_KEY, True)


def map_widget_config(
    request,
    *,
    widget_id: str = "map",
    center: tuple[float, float] | None = None,
    zoom: int | None = None,
    crosshair: bool = False,
    gps: bool = False,
    pins_url: str = "",
    confirm_url: str = "",
) -> dict:
    """Build the context for one map widget.

    center is (lat, lon).

    crosshair turns on the fixed centre crosshair and the "Confirm
    location" button, which posts to confirm_url (default: the
    reverse-geocoding confirmation view).

    gps adds the "go to my location" button and the device-location
    preference, and asks for the position as soon as the page loads
    (when the preference allows), so use it only where an
    observation is being started (§11.2), never on browse pages.

    pins_url, if given, is a GeoJSON endpoint whose features carry
    a `summary_url` property, loaded into the bottom sheet when a
    pin is tapped.
    """
    if not WIDGET_ID_RE.fullmatch(widget_id):
        raise ValueError(f"Invalid map widget id: {widget_id!r}")
    lat, lon = center or settings.MAP_DEFAULT_CENTER
    return {
        "id": widget_id,
        "config_id": f"{widget_id}-config",
        "crosshair": crosshair,
        "gps": gps,
        "confirm_url": confirm_url or reverse("location_confirm"),
        "use_device_location": uses_device_location(request),
        # Everything the JS module needs, emitted with json_script.
        "js": {
            "center": [lat, lon],
            "zoom": zoom or settings.MAP_DEFAULT_ZOOM,
            "crosshair": crosshair,
            "gps": gps,
            "pinsUrl": pins_url,
            "tileUrl": settings.MAP_TILE_URL,
            "tileAttribution": settings.MAP_TILE_ATTRIBUTION,
            "useDeviceLocation": uses_device_location(request),
        },
    }
