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

from urllib.parse import urlencode

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import translation
from sesame.utils import get_token

from authentication.models import MobilitoUser


def send_magic_link(request, user: MobilitoUser, next_url: str = "") -> None:
    """Email `user` a single-use sign-in link.

    `next_url` (already checked safe by the caller) is carried in
    the link so that the user returns to what they were doing, e.g.
    an observation in progress, even if they open the link on a
    different device.
    """
    path = reverse("auth_verify", args=[get_token(user)])
    if next_url:
        path += "?" + urlencode({"next": next_url})
    context = {
        "link": request.build_absolute_uri(path),
        "max_age_minutes": settings.SESAME_MAX_AGE // 60,
        "has_next": bool(next_url),
    }
    language = user.preferred_language or translation.get_language()
    context["language"] = language
    with translation.override(language):
        subject = render_to_string(
            "authentication/email/magic_link_subject.txt", context
        )
        text = render_to_string("authentication/email/magic_link.txt", context)
        html = render_to_string(
            "authentication/email/magic_link.html", context
        )
    send_mail(
        # Header injection guard: subjects must be one line.
        " ".join(subject.split()),
        text.strip() + "\n",
        None,
        [user.email],
        html_message=html,
    )
