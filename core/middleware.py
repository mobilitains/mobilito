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


class SyncUserLanguageMiddleware:
    """Sync an authenticated user's stored language onto the cookie.

    Must run after AuthenticationMiddleware (needs request.user) and
    before LocaleMiddleware, whose language detection reads
    request.COOKIES[settings.LANGUAGE_COOKIE_NAME] (Django has no
    session-based language store as of 5.x). Overwriting the cookie
    in-place here -- rather than only setting it in a response, which
    would be too late for LocaleMiddleware to see this same request --
    is what makes the server-stored preference (§7) take effect on
    every device/session, not just the one where it was set.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            and user.preferred_language
        ):
            request.COOKIES[settings.LANGUAGE_COOKIE_NAME] = (
                user.preferred_language
            )
        return self.get_response(request)
