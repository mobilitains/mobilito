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

import functools
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render, resolve_url
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import get_language
from django.views.decorators.http import require_POST
from django_htmx.http import HttpResponseClientRedirect

from core.forms import LocationConfirmForm
from core.geocoding import reverse_geocode
from core.maps import DEVICE_LOCATION_SESSION_KEY, WIDGET_ID_RE
from core.ratelimit import client_ip, is_rate_limited


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


@require_POST
def set_device_location(request):
    """Persist the "Use device location" preference (§11.2).

    Posted by the map widget's checkbox (htmx, no swap). A checkbox
    that's unticked isn't submitted at all, hence the default.
    """
    use = request.POST.get("use_device_location") == "on"
    if request.user.is_authenticated:
        request.user.use_device_location = use
        request.user.save(update_fields=["use_device_location"])
    else:
        request.session[DEVICE_LOCATION_SESSION_KEY] = use
    return HttpResponse(status=204)


def htmx_login_required(view):
    """login_required that also works for htmx requests.

    Plain login_required answers an htmx request with a redirect
    that htmx follows silently, pasting the sign-in page into the
    swap target. Tell htmx to navigate instead, returning to the
    page the request came from.
    """
    wrapped = login_required(view)

    @functools.wraps(view)
    def inner(request, *args, **kwargs):
        if request.htmx and not request.user.is_authenticated:
            next_url = request.htmx.current_url_abs_path or "/"
            return HttpResponseClientRedirect(
                f"{resolve_url(settings.LOGIN_URL)}?"
                + urlencode({"next": next_url})
            )
        return wrapped(request, *args, **kwargs)

    return inner


@htmx_login_required
@require_POST
def location_confirm(request):
    """Reverse-geocode the confirmed crosshair position (Phase 4).

    Returns an HTML fragment (not JSON, per the §8.2 layering
    policy) with the suggested address for the user to check and
    edit, plus the confirmed coordinates as hidden fields, for the
    form enclosing the map to submit. Invalid input still returns
    200: htmx 2 doesn't swap error responses, and the fragment
    carries its own error message.
    """
    widget = request.POST.get("widget", "map")
    if not WIDGET_ID_RE.fullmatch(widget):
        widget = "map"
    form = LocationConfirmForm(request.POST, prefix="map")
    context = {"form": form, "widget": widget, "result": None}
    if form.is_valid() and not (
        is_rate_limited(
            "location_confirm_user",
            str(request.user.pk),
            *settings.RATE_LIMIT_LOCATION_CONFIRM,
        )
        or is_rate_limited(
            "location_confirm_ip",
            client_ip(request),
            *settings.RATE_LIMIT_LOCATION_CONFIRM,
        )
    ):
        context["result"] = reverse_geocode(
            form.cleaned_data["lat"], form.cleaned_data["lon"], get_language()
        )
    return render(request, "core/partials/location_confirmed.html", context)
