"""
Copyright 2024  Francais pour une Meilleure Mobilité.

This file is part of the mobilito web application.

Mobilito is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Mobilito is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with mobilito.  If not, see <http://www.gnu.org/licenses/>.
"""

from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django_htmx.http import HttpResponseClientRedirect


@require_POST
def set_language(request):
    """Set the user's language preference (§7).

    Persists to the user record when authenticated, so the choice
    follows them across devices (synced back onto the language
    cookie on each request by SyncUserLanguageMiddleware). Always
    sets the language cookie too, which is what LocaleMiddleware
    actually reads to pick a language per request.
    """
    language = request.POST.get("language", "")
    valid_codes = {code for code, _name in settings.LANGUAGES}

    next_url = request.POST.get("next") or "/"
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}
    ):
        next_url = "/"

    if request.htmx:
        response = HttpResponseClientRedirect(next_url)
    else:
        response = HttpResponseRedirect(next_url)

    if language in valid_codes:
        if request.user.is_authenticated:
            request.user.preferred_language = language
            request.user.save(update_fields=["preferred_language"])
        response.set_cookie(
            settings.LANGUAGE_COOKIE_NAME,
            language,
            max_age=settings.LANGUAGE_COOKIE_AGE,
            path=settings.LANGUAGE_COOKIE_PATH,
            domain=settings.LANGUAGE_COOKIE_DOMAIN,
            secure=settings.LANGUAGE_COOKIE_SECURE,
            httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
            samesite=settings.LANGUAGE_COOKIE_SAMESITE,
        )

    return response
