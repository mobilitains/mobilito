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

from django.urls import path

from mobilito_app import browse, counts, reports

urlpatterns = [
    path("map/", browse.map_page, name="map"),
    path("map/here/", browse.observations_here, name="observations_here"),
    path(
        "api/observations.geojson",
        browse.observations_geojson,
        name="observations_geojson",
    ),
    path("counts/new/", counts.new_count, name="counts_new"),
    path("counts/start/", counts.start_count, name="counts_start"),
    path("counts/<int:pk>/", counts.detail, name="counts_detail"),
    path("counts/<int:pk>/summary/", counts.summary, name="counts_summary"),
    path("counts/<int:pk>/count/", counts.count, name="counts_count"),
    path("counts/<int:pk>/event/", counts.record_event, name="counts_event"),
    path("counts/<int:pk>/finish/", counts.finish, name="counts_finish"),
    path("counts/<int:pk>/discard/", counts.discard, name="counts_discard"),
    path("reports/new/", reports.new_report, name="reports_new"),
    path(
        "reports/new/location/",
        reports.confirm_location,
        name="reports_location",
    ),
    path("reports/new/submit/", reports.submit_report, name="reports_submit"),
    path("reports/<int:pk>/", reports.detail, name="reports_detail"),
    path("reports/<int:pk>/summary/", reports.summary, name="reports_summary"),
    path(
        "reports/<int:pk>/photos/<int:media_id>/",
        reports.photo,
        name="reports_photo",
    ),
]
