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
from mobilito_app.models import (
    InfrastructureTag,
    ObserverPerspective,
    TagStatus,
)

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


class ReportForm(HoneypotFormMixin, forms.Form):
    """An infrastructure report (§9.2). Photos come as request.FILES.

    lat/lon, device_* and address come from the map widget's
    confirmation fragment, as for counts. submission_id is made with
    the page (see reports.new_report) and keeps a re-sent form from
    making a second report.
    """

    # Read by the view (reports._submission_id), which ignores a value
    # that isn't a UUID: a bad key must never block the form.
    submission_id = forms.CharField(
        required=False, max_length=64, widget=forms.HiddenInput
    )

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
    perspective = forms.ChoiceField(
        choices=ObserverPerspective.choices,
        error_messages={
            "required": _("Choose how you usually pass here."),
            "invalid_choice": _("Choose how you usually pass here."),
        },
    )
    description = forms.CharField(
        required=False,
        max_length=2000,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "class": "form-control",
                "aria-describedby": "report-description-help",
            }
        ),
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=InfrastructureTag.objects.filter(status=TagStatus.ACTIVE),
        required=False,
        error_messages={
            "invalid_choice": _("Choose tags from the list shown."),
            "invalid_pk_value": _("Choose tags from the list shown."),
        },
    )
