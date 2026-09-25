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
from authentication.tokens import make_attempt_token


def send_magic_link(
    request,
    user: MobilitoUser,
    next_url: str = "",
    *,
    attempt=None,
    drop_on=None,
    base_url: str = "",
) -> None:
    """Email `user` a single-use sign-in link.

    `next_url` (already checked safe by the caller) is carried in
    the link so that the user returns to what they were doing, e.g.
    an observation in progress, even if they open the link on a
    different device.

    With `attempt`, the link confirms that provisional sign-in
    (§5.4) and only it (authentication.tokens), and stays valid for
    SIGN_IN_ATTEMPT_LINK_MAX_AGE rather than SESAME_MAX_AGE, since
    people confirm after observing. `drop_on` (a date) marks a
    reminder, which says when unconfirmed data will be deleted.
    Without a request (management commands), links are built on
    `base_url`.
    """
    if attempt is not None:
        token = make_attempt_token(attempt)
        # Marks the token as an attempt token (authentication.tokens).
        query = {"attempt": "1"}
        max_age = settings.SIGN_IN_ATTEMPT_LINK_MAX_AGE
    else:
        token = get_token(user)
        query = {}
        max_age = settings.SESAME_MAX_AGE
    if next_url:
        query["next"] = next_url
    language = user.preferred_language or translation.get_language()
    if not user.preferred_language and language in dict(settings.LANGUAGES):
        # Stored on the user only once the link is used (§5.4, §7).
        query["lang"] = language
    path = reverse("auth_verify", args=[token])
    if query:
        path += "?" + urlencode(query)
    link = (
        request.build_absolute_uri(path)
        if request is not None
        else base_url.rstrip("/") + path
    )
    context = {
        "link": link,
        "max_age_minutes": max_age // 60,
        "max_age_days": max_age // 86400,
        "has_next": bool(next_url),
        "attempt": attempt,
        "drop_on": drop_on,
    }
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
