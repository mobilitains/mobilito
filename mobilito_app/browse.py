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

# Browsing published observations (design §9.3, §21.1; roadmap
# Phase 7). Open to everyone, signed in or not, and never asks for
# the device's position: the map starts at the visitor's approximate
# position from the network edge (Cloudflare), else the default
# centre.
#
# /map/  full-page map
# /api/observations.geojson  its pins (and clusters)

import math
from dataclasses import dataclass
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.gis.db.models import GeometryField
from django.contrib.gis.db.models.functions import Transform
from django.contrib.gis.geos import Polygon
from django.core.cache import cache
from django.db.models import Avg, Count, FloatField, Func, Min
from django.db.models.functions import Cast, Floor
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from core.geo import edge_point
from core.maps import map_widget_config
from core.models import PublicationState
from mobilito_app.models import InfrastructureObservation, ModalShareSession

# Pins closer than this on screen are shown as one numbered cluster.
CLUSTER_CELL_PIXELS = 64
MARKER_PIXELS = 44
TILE_PIXELS = 256
MAX_ZOOM = 19
# Web Mercator (EPSG:3857), the projection of the map on screen.
EARTH_RADIUS_M = 6378137.0
MERCATOR_MAX_LAT = math.degrees(math.atan(math.sinh(math.pi)))
# Largest view served, in cells per side (64 × 64 px: a 4096 px
# screen). Bounds the work per request.
MAX_CELLS_PER_SIDE = 64


def mercator(lon: float, lat: float) -> tuple[float, float]:
    lat = max(-MERCATOR_MAX_LAT, min(MERCATOR_MAX_LAT, lat))
    return (
        EARTH_RADIUS_M * math.radians(lon),
        EARTH_RADIUS_M
        * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)),
    )


def unmercator(x: float, y: float) -> tuple[float, float]:
    return (
        math.degrees(x / EARTH_RADIUS_M),
        math.degrees(
            2 * math.atan(math.exp(y / EARTH_RADIUS_M)) - math.pi / 2
        ),
    )


def published_counts():
    return ModalShareSession.objects.filter(
        publication_state=PublicationState.PUBLISHED,
        finished_at__isnull=False,
    )


def published_reports():
    return InfrastructureObservation.objects.filter(
        publication_state=PublicationState.PUBLISHED
    )


# Most observations of each kind listed in one bottom sheet (a
# stack at one spot).
HERE_MAX = 50

# kind -> (queryset factory, summary URL name, detail URL name)
KINDS = {
    "count": (published_counts, "counts_summary", "counts_detail"),
    "report": (published_reports, "reports_summary", "reports_detail"),
}


def _at_most(first: int, past: int) -> tuple[int, int]:
    """A cell range cut to MAX_CELLS_PER_SIDE around its middle."""
    if past - first <= MAX_CELLS_PER_SIDE:
        return first, past
    first = (first + past) // 2 - MAX_CELLS_PER_SIDE // 2
    return first, first + MAX_CELLS_PER_SIDE


@dataclass(frozen=True)
class Viewport:
    """The map's view, grown to whole cluster cells.

    Cells are squares on screen: CLUSTER_CELL_PIXELS wide in Web
    Mercator at this zoom, numbered from the projection's origin, so
    the grid is the same whatever the view. Cell (i, j) covers
    [i, i+1) × [j, j+1) cell widths.
    """

    zoom: int
    x0: int  # first cell column (west)
    y0: int  # first cell row (south)
    x1: int  # past the last column (east)
    y1: int  # past the last row (north)

    @staticmethod
    def cell_size(zoom: int) -> float:
        """Cell width in Mercator metres at this zoom."""
        world = 2 * math.pi * EARTH_RADIUS_M
        return CLUSTER_CELL_PIXELS * world / (TILE_PIXELS * 2**zoom)

    @classmethod
    def parse(cls, bbox: str, zoom: str) -> "Viewport":
        """From Leaflet's toBBoxString() ("w,s,e,n") and the zoom.

        Raises ValueError on anything malformed. A view larger than
        MAX_CELLS_PER_SIDE is cut down around its centre (a huge
        screen, or a browser zoomed right out, still gets the
        middle). A view reaching past ±180° is cut there: the page
        sends bounds wrapped into one world.
        """
        parts = [float(part) for part in bbox.split(",")]
        if len(parts) != 4 or not all(map(math.isfinite, parts)):
            raise ValueError("bbox")
        west, south, east, north = parts
        if west > east or south > north:
            raise ValueError("bbox")
        zoom = int(zoom)
        if not 0 <= zoom <= MAX_ZOOM:
            raise ValueError("zoom")
        size = cls.cell_size(zoom)
        west = min(max(west, -180.0), 180.0)
        east = min(max(east, west), 180.0)
        south, north = max(south, -90.0), min(north, 90.0)
        (xw, ys), (xe, yn) = mercator(west, south), mercator(east, north)
        # Cells within the projected world (float noise at its edges
        # mustn't add a row beyond the poles).
        half = TILE_PIXELS * 2**zoom // CLUSTER_CELL_PIXELS // 2
        x0, x1 = _at_most(
            max(math.floor(xw / size), -half), min(math.ceil(xe / size), half)
        )
        y0, y1 = _at_most(
            max(math.floor(ys / size), -half), min(math.ceil(yn / size), half)
        )
        return cls(zoom, x0, y0, x1, y1)

    @property
    def size(self) -> float:
        return self.cell_size(self.zoom)

    @property
    def clustered(self) -> bool:
        return self.zoom < settings.MAP_CLUSTER_MAX_ZOOM

    def polygon(self) -> Polygon:
        """The whole cells covered, as a lon/lat rectangle."""
        west, south = unmercator(self.x0 * self.size, self.y0 * self.size)
        east, north = unmercator(self.x1 * self.size, self.y1 * self.size)
        return Polygon.from_bbox(
            (max(west, -180.0), south, min(east, 180.0), north)
        )

    def cache_key(self) -> str:
        return "pins:v2:%d:%d,%d,%d,%d" % (
            self.zoom,
            self.x0,
            self.y0,
            self.x1,
            self.y1,
        )


def _coordinates(lon, lat):
    # 7 decimals is about a centimetre: enough, and a smaller reply.
    return [round(lon, 7), round(lat, 7)]


def _pin(kind, pk, lon, lat):
    _, summary, detail = KINDS[kind]
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": _coordinates(lon, lat)},
        "properties": {
            "kind": kind,
            "summary_url": reverse(summary, args=[pk]),
            "url": reverse(detail, args=[pk]),
        },
    }


def _cluster(count, lon, lat):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": _coordinates(lon, lat)},
        "properties": {"kind": "cluster", "count": count},
    }


def _stack(members, lon, lat):
    """Several observations at one spot: tapping lists them all."""
    ids = {kind: [] for kind in KINDS}
    for kind, pk in members:
        # Newest first (rows come by -pk); the sheet lists at most
        # HERE_MAX of each, and says so.
        if len(ids[kind]) < HERE_MAX:
            ids[kind].append(str(pk))
    query = urlencode(
        {
            "total": len(members),
            **{kind: ",".join(pks) for kind, pks in ids.items() if pks},
        }
    )
    feature = _cluster(len(members), lon, lat)
    feature["properties"]["summary_url"] = (
        reverse("observations_here") + "?" + query
    )
    return feature


def _pins(viewport: Viewport) -> list:
    area = viewport.polygon()
    if not viewport.clustered:
        # Zoomed in far enough that every pin fits on screen.
        # Observations at the very same spot (repeat counts share
        # their Location) would hide each other: one marker for them
        # all, opening the list of them. (Only exact matches: one a
        # few metres away stays its own pin, and may sit under the
        # marker at zoom 17; the list view reaches it.)
        spots = {}
        for kind, (queryset, _, _) in KINDS.items():
            rows = (
                queryset()
                .filter(location__point__within=area)
                .order_by("-pk")
                .values_list("pk", "location__point")[: settings.MAP_PINS_MAX]
            )
            for pk, point in rows:
                spot = tuple(_coordinates(point.x, point.y))
                spots.setdefault(spot, []).append((kind, pk))
        return [
            (
                _pin(*members[0], *spot)
                if len(members) == 1
                else _stack(members, *spot)
            )
            for spot, members in spots.items()
        ]

    # Group by cell in the database, then merge both kinds. A cluster
    # sits at the mean of its members, so tapping it zooms in on
    # them; a cell holding one observation is simply its pin.
    size = viewport.size
    # Points are geography: cast to geometry to project them.
    geometry = Cast("location__point", GeometryField(srid=4326))
    projected = Transform(geometry, 3857)
    cells = {}
    for kind, (queryset, _, _) in KINDS.items():
        rows = (
            queryset()
            .filter(location__point__within=area)
            .annotate(
                i=Floor(_coordinate(projected, "ST_X") / size),
                j=Floor(_coordinate(projected, "ST_Y") / size),
            )
            .values("i", "j")
            .annotate(
                n=Count("pk"),
                first=Min("pk"),
                x=Avg(_coordinate(projected, "ST_X")),
                y=Avg(_coordinate(projected, "ST_Y")),
            )
        )
        for row in rows:
            key = (int(row["i"]), int(row["j"]))
            cell = cells.setdefault(key, {"n": 0, "x": 0.0, "y": 0.0})
            # Mean over both kinds, weighted by their counts.
            _merge(cell, row)
            cell["pin"] = (kind, row["first"])
    features = []
    for cell in _merge_close(cells, size):
        lon, lat = unmercator(cell["x"], cell["y"])
        if cell["n"] == 1:
            features.append(_pin(*cell["pin"], lon, lat))
        else:
            features.append(_cluster(cell["n"], lon, lat))
    return features


def _coordinate(expression, function):
    return Func(expression, function=function, output_field=FloatField())


def _merge(into, cell):
    total = into["n"] + cell["n"]
    into["x"] += (cell["x"] - into["x"]) * cell["n"] / total
    into["y"] += (cell["y"] - into["y"]) * cell["n"] / total
    into["n"] = total


def _merge_close(cells: dict, size: float) -> list:
    """Merge groups that ended up overlapping on screen.

    Members near a cell's edge put its mean close to the next cell's:
    two clusters (or a pin and a cluster) would then sit on top of
    each other. Biggest first, a group within a marker's width of one in
    a neighbouring cell joins it.
    """
    # Closer than a marker's width (44 px, a finger), they'd overlap.
    overlap = size * MARKER_PIXELS / CLUSTER_CELL_PIXELS
    groups = {}  # cell key -> group that started there
    for key, cell in sorted(cells.items(), key=lambda item: -item[1]["n"]):
        i, j = key
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                group = groups.get((i + di, j + dj))
                if (
                    group is not None
                    and abs(group["x"] - cell["x"]) < overlap
                    and abs(group["y"] - cell["y"]) < overlap
                ):
                    _merge(group, cell)
                    break
            else:
                continue
            break
        else:
            groups[key] = dict(cell)
    return list(groups.values())


@require_GET
def observations_geojson(request):
    """Published observations in the map's view, as GeoJSON.

    ?bbox=west,south,east,north&zoom=z. Below MAP_CLUSTER_MAX_ZOOM,
    nearby observations come as numbered clusters. Cached briefly
    (snapped to the cluster grid) so many viewers of one area share
    the work.
    """
    try:
        viewport = Viewport.parse(
            request.GET.get("bbox", ""), request.GET.get("zoom", "")
        )
    except ValueError:
        return HttpResponseBadRequest("bbox=w,s,e,n and zoom=0..19")
    key = viewport.cache_key()
    features = cache.get(key)
    if features is None:
        features = _pins(viewport)
        cache.set(key, features, settings.MAP_PINS_CACHE_SECONDS)
    response = JsonResponse(
        {"type": "FeatureCollection", "features": features},
        content_type="application/geo+json",
    )
    response["Cache-Control"] = (
        f"public, max-age={settings.MAP_PINS_CACHE_SECONDS}"
    )
    return response


def _float(value, low, high):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and low <= number <= high else None


@require_GET
def map_page(request):
    """Full-page map of published observations (§9.3).

    ?lat=&lon=&zoom= open it on a given spot (links from an
    observation); otherwise it starts near the visitor, from the
    network's idea of where they are, never from the device.
    """
    lat = _float(request.GET.get("lat"), -90, 90)
    lon = _float(request.GET.get("lon"), -180, 180)
    zoom = _float(request.GET.get("zoom"), 0, MAX_ZOOM)
    center = None
    if lat is not None and lon is not None:
        center = (lat, lon)
    else:
        near = edge_point(request)
        if near is not None:
            center = (near.y, near.x)
    return render(
        request,
        "mobilito_app/browse/map.html",
        {
            "map": map_widget_config(
                request,
                widget_id="browse-map",
                center=center,
                zoom=int(zoom) if zoom is not None else None,
                pins_url=reverse("observations_geojson"),
            ),
        },
    )


def _ids(value: str) -> list[int]:
    """Ids from "1,2,3"; anything else (from a crafted URL) ignored."""
    return [
        int(part)
        for part in value.split(",")
        if part.isascii() and part.isdigit() and len(part) <= 18
    ][:HERE_MAX]


@require_GET
def observations_here(request):
    """Several published observations at one spot, for the sheet.

    ?count=1,2&report=3&total=n. Anything not published is left out.
    total is how many are at the spot, to say when the list is cut.
    """
    counts = (
        published_counts()
        .select_related("location")
        .filter(pk__in=_ids(request.GET.get("count", "")))
    )
    reports = (
        published_reports()
        .select_related("location")
        .filter(pk__in=_ids(request.GET.get("report", "")))
    )
    for count in counts:
        # What tells repeat counts at one spot apart in the list.
        count.total = sum(count.totals().values())
        count.minutes = round(
            (count.finished_at - count.started_at).total_seconds() / 60
        )
    items = sorted(
        [("count", c, c.started_at) for c in counts]
        + [("report", r, r.created_at) for r in reports],
        key=lambda item: item[2],
        reverse=True,
    )
    return render(
        request,
        "mobilito_app/browse/here.html",
        {
            "items": [(kind, item) for kind, item, _ in items],
            "cut": (_ids(request.GET.get("total", "")) or [0])[0] > len(items),
        },
    )
