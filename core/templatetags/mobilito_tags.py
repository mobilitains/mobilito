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

register = template.Library()


@register.inclusion_tag("includes/unvalidated_banner.html", takes_context=True)
def unvalidated_banner(context):
    """Gently prompt a signed-in user to confirm their email (§5.4).

    Renders nothing unless the user is authenticated but hasn't yet
    proved control of their address. The magic-link flow validates
    the address as it signs the user in, so today this only fires
    for users signed in some other way (admin-created, and later
    password sign-in or deferred validation, §5.4).
    """
    request = context.get("request")
    user = getattr(request, "user", None)
    show = bool(
        user is not None and user.is_authenticated and not user.email_validated
    )
    return {
        "show": show,
        "email": user.email if show else "",
        # Don't send them back to an auth page after signing in.
        "next": (
            request.get_full_path()
            if show and not request.path.startswith("/auth/")
            else ""
        ),
    }
