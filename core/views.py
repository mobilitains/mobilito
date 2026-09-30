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
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import get_language
from django.views.decorators.http import require_POST
from django_htmx.http import HttpResponseClientRedirect

from authentication.provisional import observer_required
from core.forms import LocationConfirmForm
from core.geocoding import get_geocoder, reverse_geocode
from core.maps import (
    DEVICE_LOCATION_SESSION_KEY,
    WIDGET_ID_RE,
    tile_attribution,
)
from core.ratelimit import client_ip, is_rate_limited


def _back_to_next(request):
    """Redirect to the POSTed "next" if it's on this site, else home."""
    requested_next = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(
        requested_next,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = requested_next
    else:
        next_url = "/"
    if request.htmx:
        return HttpResponseClientRedirect(next_url)
    return HttpResponseRedirect(next_url)


@require_POST
def set_language(request):
    """Set the user's language preference (§7).

    Persists to the user record when authenticated, so the choice
    follows them across devices (synced back onto the language
    cookie on each request by SyncUserLanguageMiddleware). Always
    sets the language cookie too, which is what LocaleMiddleware
    actually reads to pick a language per request.
    """
    # Take the code from settings rather than echoing the request,
    # so only a configured value ever reaches the cookie.
    requested = request.POST.get("language", "")
    language = next(
        (code for code, _name in settings.LANGUAGES if code == requested),
        None,
    )

    response = _back_to_next(request)
    if language is not None:
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
def set_theme(request):
    """Switch between light and dark mode (doc/colours.md).

    Per device: someone may want dark on a laptop at night and light
    on a phone in the sun, so it's a cookie, not a user setting.
    """
    response = _back_to_next(request)
    theme = request.POST.get("theme")
    if theme in ("light", "dark"):
        response.set_cookie(
            settings.THEME_COOKIE_NAME,
            theme,
            max_age=settings.THEME_COOKIE_AGE,
            secure=request.is_secure(),
            httponly=True,
            samesite="Lax",
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


def _observer_key(observer) -> str:
    if observer.user is not None:
        return f"user:{observer.user.pk}"
    return f"attempt:{observer.attempt.pk}"


def confirm_location_context(request) -> dict:
    """Validate a posted crosshair position and reverse-geocode it.

    Shared by the plain confirmation below and flows that add to it
    (the report form's tag refresh).
    """
    widget = request.POST.get("widget", "map")
    if not WIDGET_ID_RE.fullmatch(widget):
        widget = "map"
    form = LocationConfirmForm(request.POST, prefix="map")
    context = {"form": form, "widget": widget, "result": None}
    if form.is_valid() and not (
        is_rate_limited(
            "location_confirm_user",
            _observer_key(request.observer),
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
    return context


@observer_required
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
    context = confirm_location_context(request)
    return render(request, "core/partials/location_confirmed.html", context)


def credits_page(request):
    """Credits and licences: map data, addresses and our own source.

    Attribution for OpenStreetMap data (ODbL) and the source-code
    offer the AGPL requires of a network service.
    """
    geocoder = get_geocoder()
    return render(
        request,
        "core/credits.html",
        {
            "tile_attribution": tile_attribution(),
            "address_credit": geocoder.credit,
            "address_credit_url": geocoder.credit_url,
            "source_code_url": settings.SOURCE_CODE_URL,
        },
    )
