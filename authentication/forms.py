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


class MagicLinkRequestForm(HoneypotFormMixin, forms.Form):
    email = forms.EmailField(
        label=_("Your email address"),
        widget=forms.EmailInput(
            attrs={
                "autocomplete": "email",
                "class": "form-control form-control-lg",
            }
        ),
    )
    next = forms.CharField(required=False, widget=forms.HiddenInput)

    def full_clean(self):
        super().full_clean()
        if "email" in self.errors:
            attrs = self.fields["email"].widget.attrs
            attrs["class"] += " is-invalid"
            attrs["aria-invalid"] = "true"
            attrs["aria-describedby"] = self["email"].id_for_label + "-errors"


class MagicLinkConfirmForm(forms.Form):
    remember = forms.BooleanField(
        required=False,
        initial=True,
        label=_("Stay signed in on this device"),
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
