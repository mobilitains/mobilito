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
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from authentication.models import MobilitoUser
from core.geo import make_point
from core.models import Location, PublicationState
from mobilito_app.browse import (
    HERE_MAX,
    MAX_CELLS_PER_SIDE,
    Viewport,
    mercator,
    unmercator,
)
from mobilito_app.models import (
    InfrastructureObservation,
    InfrastructureTag,
    ModalShareSession,
)

PUBLISHED = PublicationState.PUBLISHED
# Around Nantes: -1.6..-1.5 E, 47.2..47.25 N (a phone screen at
# zoom 12 to 14).
NANTES_BBOX = "-1.6,47.2,-1.5,47.25"
# A street-level view (zoom 17 or more) of part of it.
STREET_BBOX = "-1.565,47.205,-1.545,47.225"


def place(lat, lon, address=""):
    return Location.objects.create(
        point=make_point(lat, lon), reverse_geocoded_address=address
    )


def count_at(lat, lon, state=PUBLISHED, finished=True, **fields):
    now = timezone.now()
    return ModalShareSession.objects.create(
        location=place(lat, lon, fields.pop("address", "")),
        started_at=now - timedelta(minutes=15),
        finished_at=now if finished else None,
        publication_state=state,
        **fields,
    )


def report_at(lat, lon, state=PUBLISHED, **fields):
    return InfrastructureObservation.objects.create(
        location=place(lat, lon, fields.pop("address", "")),
        observer_perspective="bike",
        publication_state=state,
        **fields,
    )


class BrowseTestCase(TestCase):
    def setUp(self):
        cache.clear()

    def pins(self, bbox=STREET_BBOX, zoom=17):
        response = self.client.get(
            reverse("observations_geojson"), {"bbox": bbox, "zoom": zoom}
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["features"]


class GeoJSONTests(BrowseTestCase):
    def test_only_published_observations_in_view(self):
        shown_count = count_at(47.21, -1.55)
        shown_report = report_at(47.22, -1.56)
        count_at(47.21, -1.551, state=PublicationState.PENDING_MODERATION)
        count_at(47.211, -1.55, finished=False)
        report_at(47.22, -1.561, state=PublicationState.SANDBOXED)
        report_at(47.24, -1.51)  # outside the view
        features = self.pins()
        urls = sorted(f["properties"]["url"] for f in features)
        self.assertEqual(
            urls,
            sorted(
                [
                    reverse("counts_detail", args=[shown_count.pk]),
                    reverse("reports_detail", args=[shown_report.pk]),
                ]
            ),
        )
        report = next(
            f for f in features if f["properties"]["kind"] == "report"
        )
        self.assertEqual(report["geometry"]["coordinates"], [-1.56, 47.22])
        self.assertEqual(
            report["properties"]["summary_url"],
            reverse("reports_summary", args=[shown_report.pk]),
        )

    def test_nearby_observations_cluster_when_zoomed_out(self):
        count_at(47.2100, -1.5500)
        report_at(47.2101, -1.5501)
        report_at(47.2102, -1.5499)
        alone = report_at(47.24, -1.51)
        features = self.pins(bbox=NANTES_BBOX, zoom=12)
        clusters = [
            f for f in features if f["properties"]["kind"] == "cluster"
        ]
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["properties"]["count"], 3)
        self.assertNotIn("summary_url", clusters[0]["properties"])
        # At the mean of its members (both kinds), not a grid point.
        lon, lat = clusters[0]["geometry"]["coordinates"]
        self.assertAlmostEqual(lon, -1.55, places=6)
        self.assertAlmostEqual(lat, 47.2101, places=6)
        singles = [f for f in features if f["properties"]["kind"] != "cluster"]
        self.assertEqual(len(singles), 1)
        # A lone observation keeps its own position, not the cell's.
        self.assertEqual(singles[0]["geometry"]["coordinates"], [-1.51, 47.24])
        self.assertEqual(
            singles[0]["properties"]["url"],
            reverse("reports_detail", args=[alone.pk]),
        )

    def test_groups_either_side_of_a_cell_edge_merge(self):
        size = Viewport.cell_size(12)
        # A cell edge (in Mercator) near Nantes, as a longitude.
        x = math.floor(mercator(-1.55, 47.21)[0] / size) * size
        near = size / 20
        west, _ = unmercator(x - near, 0)
        east, _ = unmercator(x + near, 0)
        # Two pairs just either side of it: without merging, two
        # clusters would sit almost on top of each other.
        for lon in (west, west, east):
            report_at(47.2101, lon)
        count_at(47.2101, east)
        features = self.pins(bbox=NANTES_BBOX, zoom=12)
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["properties"]["count"], 4)

    def test_a_cluster_cut_by_the_view_edge_counts_all_its_members(self):
        viewport = Viewport.parse(NANTES_BBOX, "12")
        _, _, east, _ = viewport.polygon().extent
        # Three just inside the covered cells' east edge, and the
        # request's own east edge cutting through their cell.
        for offset in (0.0005, 0.0006, 0.0007):
            report_at(47.22, east - offset)
        features = self.pins(bbox=f"-1.6,47.2,{east - 0.00065},47.25", zoom=12)
        self.assertEqual([f["properties"].get("count") for f in features], [3])

    def test_too_large_a_view_still_gets_its_middle(self):
        count_at(47.21, -1.55)
        count_at(10.0, 10.0)
        features = self.pins(bbox="-2.5,46.3,-0.6,48.1", zoom=17)
        self.assertEqual(len(features), 1)

    def test_observations_at_the_same_spot_list_together(self):
        first = count_at(47.21, -1.55)
        again = ModalShareSession.objects.create(
            location=first.location,
            started_at=first.started_at,
            finished_at=first.finished_at,
            publication_state=PUBLISHED,
        )
        report = report_at(47.21, -1.55)
        features = self.pins()
        self.assertEqual(len(features), 1)
        stack = features[0]["properties"]
        self.assertEqual(stack["kind"], "cluster")
        self.assertEqual(stack["count"], 3)
        response = self.client.get(stack["summary_url"])
        for url in (
            reverse("counts_detail", args=[first.pk]),
            reverse("counts_detail", args=[again.pk]),
            reverse("reports_detail", args=[report.pk]),
        ):
            self.assertContains(response, url)

    @override_settings(MAP_PINS_MAX=2)
    def test_street_level_keeps_the_newest_when_there_are_too_many(self):
        reports = [report_at(47.21, -1.55 + i * 0.001) for i in range(3)]
        urls = {f["properties"]["url"] for f in self.pins()}
        self.assertEqual(
            urls,
            {reverse("reports_detail", args=[r.pk]) for r in reports[1:]},
        )

    def test_no_clusters_when_zoomed_in(self):
        # A few metres apart: separate pins at street level.
        count_at(47.2100, -1.5500)
        report_at(47.2100, -1.5501)
        kinds = sorted(f["properties"]["kind"] for f in self.pins())
        self.assertEqual(kinds, ["count", "report"])

    def test_bad_requests(self):
        url = reverse("observations_geojson")
        for params in (
            {},
            {"bbox": "a,b,c,d", "zoom": 12},
            {"bbox": "1,2,3", "zoom": 12},
            {"bbox": "3,2,1,4", "zoom": 12},
            {"bbox": "nan,1,2,3", "zoom": 12},
            {"bbox": NANTES_BBOX, "zoom": 25},
            {"bbox": NANTES_BBOX, "zoom": "x"},
        ):
            with self.subTest(params=params):
                self.assertEqual(self.client.get(url, params).status_code, 400)

    def test_whole_world_is_accepted(self):
        count_at(47.21, -1.55)
        self.assertEqual(len(self.pins(bbox="-540,-100,540,100", zoom=0)), 1)

    @override_settings(MAP_PINS_CACHE_SECONDS=60)
    def test_cached_briefly_and_publicly(self):
        response = self.client.get(
            reverse("observations_geojson"),
            {"bbox": NANTES_BBOX, "zoom": 14},
        )
        self.assertIn("max-age=60", response["Cache-Control"])
        self.assertEqual(response["Content-Type"], "application/geo+json")
        count_at(47.21, -1.55)
        # Same area, slightly moved: the same snapped cache entry.
        self.assertEqual(
            self.pins(bbox="-1.6001,47.2,-1.5,47.25", zoom=14), []
        )


class ViewportTests(TestCase):
    def test_grows_outward_to_whole_cells(self):
        viewport = Viewport.parse(NANTES_BBOX, "12")
        west, south, east, north = viewport.polygon().extent
        self.assertLessEqual(west, -1.6)
        self.assertLessEqual(south, 47.2)
        self.assertGreaterEqual(east, -1.5)
        self.assertGreaterEqual(north, 47.25)

    def test_panning_within_a_cell_keeps_the_same_grid(self):
        # Both directions: the grid doesn't depend on the view.
        base = Viewport.parse(NANTES_BBOX, "12")
        for bbox in (
            "-1.6001,47.2,-1.5001,47.25",
            "-1.6,47.2001,-1.5,47.2501",
        ):
            with self.subTest(bbox=bbox):
                moved = Viewport.parse(bbox, "12")
                self.assertEqual(moved.cache_key(), base.cache_key())

    def test_too_large_a_view_is_cut_around_its_centre(self):
        # The whole world at street level: no screen shows that.
        viewport = Viewport.parse("-180,-85,180,85", "16")
        self.assertEqual(viewport.x1 - viewport.x0, MAX_CELLS_PER_SIDE)
        self.assertEqual(viewport.y1 - viewport.y0, MAX_CELLS_PER_SIDE)
        west, south, east, north = viewport.polygon().extent
        self.assertLess(west, 0)
        self.assertGreater(east, 0)
        self.assertLess(south, 0)
        self.assertGreater(north, 0)

    def test_a_view_wholly_past_the_antimeridian_is_empty(self):
        viewport = Viewport.parse("-540,-100,-400,100", "0")
        self.assertLessEqual(viewport.x0, viewport.x1)

    def test_poles_stop_at_the_projections_edge(self):
        viewport = Viewport.parse("-180,-90,180,90", "0")
        _, south, _, north = viewport.polygon().extent
        self.assertAlmostEqual(north, 85.0511287798, places=6)
        self.assertAlmostEqual(south, -85.0511287798, places=6)

    def test_past_the_antimeridian_is_cut(self):
        viewport = Viewport.parse("170,40,200,50", "3")
        west, _, east, _ = viewport.polygon().extent
        self.assertLessEqual(east, 180)
        self.assertEqual(Viewport.parse("190,40,220,50", "3").x0, viewport.x1)


class SummaryTests(BrowseTestCase):
    def test_count_summary(self):
        session = count_at(
            47.21, -1.55, total_cyclist=7, address="Quai de la Fosse"
        )
        response = self.client.get(
            reverse("counts_summary", args=[session.pk])
        )
        self.assertContains(response, "Quai de la Fosse")
        self.assertContains(response, "7 people and vehicles")
        self.assertContains(
            response, reverse("counts_detail", args=[session.pk])
        )
        self.assertNotContains(response, "<html")

    def test_report_summary(self):
        report = report_at(47.21, -1.55, description="Kerb too high")
        report.tags.add(InfrastructureTag.objects.create(label="Kerb"))
        response = self.client.get(
            reverse("reports_summary", args=[report.pk])
        )
        self.assertContains(response, "Kerb too high")
        self.assertContains(response, "Kerb")
        self.assertContains(
            response, reverse("reports_detail", args=[report.pk])
        )

    def test_unpublished_summaries_are_hidden(self):
        session = count_at(47.21, -1.55, state=PublicationState.SANDBOXED)
        report = report_at(47.21, -1.55, state=PublicationState.SANDBOXED)
        for url in (
            reverse("counts_summary", args=[session.pk]),
            reverse("reports_summary", args=[report.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_owner_sees_own_unpublished_summary(self):
        user = MobilitoUser.objects.create_user("me@example.com")
        report = report_at(
            47.21, -1.55, state=PublicationState.PENDING_MODERATION, user=user
        )
        self.client.force_login(user)
        response = self.client.get(
            reverse("reports_summary", args=[report.pk])
        )
        self.assertEqual(response.status_code, 200)


class HereTests(BrowseTestCase):
    def test_lists_only_published_observations(self):
        shown = report_at(47.21, -1.55, description="Shown")
        hidden = report_at(
            47.21, -1.55, state=PublicationState.SANDBOXED, description="Hid"
        )
        open_count = count_at(47.21, -1.55, finished=False)
        response = self.client.get(
            reverse("observations_here"),
            {
                "report": f"{shown.pk},{hidden.pk},x",
                "count": str(open_count.pk),
            },
        )
        self.assertContains(response, "Shown")
        self.assertNotContains(response, "Hid")
        self.assertNotContains(
            response, reverse("counts_detail", args=[open_count.pk])
        )

    def test_crafted_ids_are_ignored(self):
        shown = report_at(47.21, -1.55, description="Shown")
        response = self.client.get(
            reverse("observations_here"),
            {"report": f"²,{'9' * 5000},{shown.pk},-1"},
        )
        self.assertContains(response, "Shown")

    def test_counts_can_be_told_apart(self):
        count_at(47.21, -1.55, total_cyclist=7)
        count_at(47.21, -1.55, total_car=12)
        response = self.client.get(
            reverse("observations_here"),
            {
                "count": ",".join(
                    str(pk)
                    for pk in ModalShareSession.objects.values_list(
                        "pk", flat=True
                    )
                )
            },
        )
        self.assertContains(response, "7 counted")
        self.assertContains(response, "12 counted")
        self.assertContains(response, "15 minutes", count=2)

    @override_settings(MAP_PINS_MAX=1000)
    def test_a_big_stack_lists_the_latest_and_says_so(self):
        first = report_at(47.21, -1.55)
        for _ in range(HERE_MAX + 4):
            InfrastructureObservation.objects.create(
                location=first.location,
                observer_perspective="ped",
                publication_state=PUBLISHED,
            )
        stack = self.pins()[0]["properties"]
        self.assertEqual(stack["count"], HERE_MAX + 5)
        response = self.client.get(stack["summary_url"])
        self.assertContains(response, f"Showing the latest {HERE_MAX}.")
        self.assertNotContains(
            response, reverse("reports_detail", args=[first.pk])
        )

    def test_nothing_left_to_show(self):
        response = self.client.get(reverse("observations_here"))
        self.assertContains(response, "aren’t shown any more")


class MapPageTests(BrowseTestCase):
    def test_open_to_everyone_without_gps(self):
        response = self.client.get(reverse("map"))
        self.assertContains(response, 'data-mobilito-map="browse-map"')
        self.assertContains(response, reverse("observations_geojson"))
        self.assertNotContains(response, "data-map-gps")
        self.assertNotContains(response, "Confirm location")

    def test_starts_on_a_given_spot(self):
        response = self.client.get(
            reverse("map"), {"lat": "47.1", "lon": "-1.2", "zoom": "16"}
        )
        config = response.context["map"]["js"]
        self.assertEqual(config["center"], [47.1, -1.2])
        self.assertEqual(config["zoom"], 16)

    def test_starts_near_the_visitor_from_the_network(self):
        response = self.client.get(
            reverse("map"),
            HTTP_CF_IPLATITUDE="48.85",
            HTTP_CF_IPLONGITUDE="2.35",
        )
        self.assertEqual(
            response.context["map"]["js"]["center"], [48.85, 2.35]
        )

    def test_bad_parameters_are_ignored(self):
        response = self.client.get(
            reverse("map"), {"lat": "95", "lon": "x", "zoom": "nan"}
        )
        self.assertEqual(response.status_code, 200)

    def test_home_links_here(self):
        self.assertContains(self.client.get(reverse("home")), reverse("map"))
