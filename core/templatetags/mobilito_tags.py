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

from django import template
from urllib.parse import urlencode

from django.urls import reverse

register = template.Library()


@register.inclusion_tag("includes/unvalidated_banner.html", takes_context=True)
def unvalidated_banner(context):
    """Gently prompt someone to confirm their email address (§5.4).

    Shown to a provisional ("probably signed in") session whose
    attempt isn't confirmed yet, and to a signed-in user whose
    address isn't validated (e.g. admin-created). Renders nothing
    otherwise.
    """
    from authentication.provisional import get_observer

    request = context.get("request")
    empty = {"show": False}
    if request is None or not hasattr(request, "session"):
        return empty
    observer = get_observer(request)
    next_url = (
        request.get_full_path()
        if not request.path.startswith("/auth/")
        else ""
    )
    if observer.user is not None:
        if observer.user.email_validated:
            return empty
        return {
            "show": True,
            "signed_in": True,
            "email": observer.user.email,
            "resend_url": reverse("auth_start"),
            "next": next_url,
        }
    if request.path.startswith("/auth/"):
        # Those pages are about exactly this; the banner would repeat
        # (or link to) them.
        return empty
    if observer.attempt is not None and not observer.attempt.is_confirmed:
        return {
            "show": True,
            "email": observer.attempt.email,
            "resend_url": reverse("auth_observe_resend"),
            "change_url": reverse("auth_observe")
            + "?"
            + urlencode({"change": "1", "next": next_url or "/"}),
            "next": next_url,
        }
    return empty
