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

import math

from django.contrib.gis.geos import Point

EARTH_RADIUS_METERS = 6_371_000


def make_point(lat: float, lon: float) -> Point:
    """A WGS84 point from latitude/longitude (GEOS wants x=lon)."""
    return Point(lon, lat, srid=4326)


def distance_meters(a: Point, b: Point) -> float:
    """Great-circle (haversine) distance between two WGS84 points.

    Plenty accurate for "is the phone near the stated spot" checks,
    and needs no database round trip.
    """
    lat1, lon1 = math.radians(a.y), math.radians(a.x)
    lat2, lon2 = math.radians(b.y), math.radians(b.x)
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(h))


def edge_point(request):
    """The client's position per Cloudflare's visitor location headers.

    Present only when the "Add visitor location headers" managed
    transform is on (§11.1: recorded as a secondary signal needing no
    permission). None otherwise.
    """
    try:
        lat = float(request.META["HTTP_CF_IPLATITUDE"])
        lon = float(request.META["HTTP_CF_IPLONGITUDE"])
    except (KeyError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return make_point(lat, lon)
