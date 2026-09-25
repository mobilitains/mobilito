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


class HoneypotFormMixin(forms.Form):
    """Add a honeypot field (§16) that humans never see or fill in.

    Templates render it with the includes/honeypot.html include,
    which hides it off-screen (not display:none, which some bots
    detect) and from assistive technology. A form whose honeypot is
    filled in is still valid, so views can pretend to succeed rather
    than tell the bot what gave it away; check is_bot() after
    is_valid().
    """

    HONEYPOT_FIELD = "hp_field"

    # Deliberately meaningless name: a name like "website" invites
    # browser autofill, which would silently lock a real person out.
    hp_field = forms.CharField(
        required=False,
        label=_("Leave this field empty"),
        widget=forms.TextInput(
            attrs={"autocomplete": "off", "tabindex": "-1"}
        ),
    )

    def is_bot(self) -> bool:
        return bool(self.cleaned_data.get(self.HONEYPOT_FIELD))
