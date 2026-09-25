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

from django import forms
from django.utils.translation import gettext_lazy as _

from core.forms import HoneypotFormMixin

CONFIRM_FIRST = _("Tap “Confirm location” first.")


class CountStartForm(HoneypotFormMixin, forms.Form):
    """Starts a modal share count at the confirmed spot.

    lat/lon, the device_* evidence and the address come from the map
    widget's confirmation fragment (core/partials/location_confirmed),
    which only exists once the user has confirmed a position.
    """

    lat = forms.FloatField(
        min_value=-90,
        max_value=90,
        error_messages={"required": CONFIRM_FIRST},
        widget=forms.HiddenInput,
    )
    lon = forms.FloatField(
        min_value=-180,
        max_value=180,
        error_messages={"required": CONFIRM_FIRST},
        widget=forms.HiddenInput,
    )
    device_lat = forms.FloatField(required=False, min_value=-90, max_value=90)
    device_lon = forms.FloatField(
        required=False, min_value=-180, max_value=180
    )
    device_accuracy = forms.FloatField(required=False, min_value=0)
    address = forms.CharField(required=False, max_length=255, strip=True)
    origin = forms.IntegerField(required=False, widget=forms.HiddenInput)
