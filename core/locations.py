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

from django.utils.translation import get_language

from core.geocoding import reverse_geocode


def new_location_fields(point, address: str) -> dict:
    """Fields for a new Location at a confirmed point (§11.1, §20.2).

    Area metadata comes from reverse geocoding (cached, so usually
    free after "Confirm location"). The address the user typed is
    kept as theirs only if it differs from the suggestion. Call
    outside transactions: it may reach a geocoding service.
    """
    suggestion = reverse_geocode(point.y, point.x, get_language())
    suggested = suggestion.address if suggestion else ""
    return {
        "point": point,
        "user_entered_address": address if address != suggested else "",
        "reverse_geocoded_address": suggested,
        "country": suggestion.country if suggestion else "",
        "region": suggestion.region if suggestion else "",
        "department": suggestion.department if suggestion else "",
        "commune": suggestion.commune if suggestion else "",
    }
