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

import logging
import smtplib

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import get_language_from_request
from django.views.decorators.http import require_http_methods, require_POST
from sesame.utils import get_user

from authentication.email import send_magic_link
from authentication.forms import MagicLinkConfirmForm, MagicLinkRequestForm
from authentication.models import get_user_by_email, normalise_email
from core.ratelimit import client_ip, is_rate_limited

logger = logging.getLogger(__name__)

SENT_EMAIL_SESSION_KEY = "auth_magic_link_email"
SENT_NEXT_SESSION_KEY = "auth_magic_link_next"


def safe_next_url(request, url: str | None) -> str:
    """Return `url` if it's a safe same-site path to redirect to, else "".

    Only absolute paths are accepted: anything else would be passed
    to reverse() by redirect() and could 500 after sign-in.
    """
    if (
        url
        and url.startswith("/")
        and url_has_allowed_host_and_scheme(
            url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        )
    ):
        return url
    return ""


def _too_many_requests(request, form):
    form.add_error(
        None,
        _(
            "There have been several requests for links in a short "
            "time. Check your inbox and spam folder for one we've "
            "already sent. If it no longer works, you can ask for a "
            "new one within the hour."
        ),
    )
    return render(
        request, "authentication/start.html", {"form": form}, status=429
    )


@require_http_methods(["GET", "POST"])
def start(request):
    """Ask for an email address and send a magic link (§5.2)."""
    if request.method != "POST":
        form = MagicLinkRequestForm(
            initial={
                "next": safe_next_url(request, request.GET.get("next")),
                "email": request.GET.get("email", ""),
            }
        )
        return render(request, "authentication/start.html", {"form": form})

    form = MagicLinkRequestForm(request.POST)
    if is_rate_limited(
        "auth_start_ip",
        client_ip(request),
        *settings.RATE_LIMIT_AUTH_START_PER_IP,
    ):
        return _too_many_requests(request, form)
    if not form.is_valid():
        return render(request, "authentication/start.html", {"form": form})

    email = normalise_email(form.cleaned_data["email"])
    if form.is_bot():
        # Look exactly like success, but create and send nothing.
        # Warning, not info: autofill could conceivably fill it in
        # for a real person, who would then never get their email.
        logger.warning("Honeypot filled on auth start; ignoring")
    else:
        if is_rate_limited(
            "auth_start_email",
            email,
            *settings.RATE_LIMIT_AUTH_START_PER_EMAIL,
        ):
            return _too_many_requests(request, form)
        user = get_user_by_email(email)
        if not user.preferred_language:
            # Remember the language they asked in, so the email and
            # later visits match it (§7), whichever device opens the
            # link.
            user.preferred_language = get_language_from_request(request)
            user.save(update_fields=["preferred_language"])
        if user.is_active:
            try:
                send_magic_link(
                    request,
                    user,
                    safe_next_url(request, form.cleaned_data["next"]),
                )
            except (smtplib.SMTPException, OSError):
                logger.exception("Failed to send magic link")
                form.add_error(
                    None,
                    _(
                        "We couldn't send the email just now. Please "
                        "try again in a few minutes."
                    ),
                )
                return render(
                    request,
                    "authentication/start.html",
                    {"form": form},
                    status=503,
                )
    request.session[SENT_EMAIL_SESSION_KEY] = email
    request.session[SENT_NEXT_SESSION_KEY] = safe_next_url(
        request, form.cleaned_data["next"]
    )
    return redirect("auth_sent")


def sent(request):
    """Tell the user to check their email (post/redirect/get target)."""
    return render(
        request,
        "authentication/sent.html",
        {
            "email": request.session.get(SENT_EMAIL_SESSION_KEY, ""),
            "next": request.session.get(SENT_NEXT_SESSION_KEY, ""),
            "max_age_minutes": settings.SESAME_MAX_AGE // 60,
        },
    )


@require_http_methods(["GET", "POST"])
def verify(request, token):
    """Sign in from a magic link.

    GET only checks the token and shows a one-tap confirmation page;
    the token is consumed (it's single-use) by the POST. Email
    security scanners that follow links would otherwise burn the
    link before the user ever clicks it. The confirmation step is
    also where the user chooses whether this device remembers them,
    which is a per-device choice.
    """
    next_url = safe_next_url(request, request.GET.get("next"))
    if request.method != "POST":
        user = get_user(token, update_last_login=False)
        if user is None:
            return _invalid_link(request, next_url)
        return render(
            request,
            "authentication/verify.html",
            {"form": MagicLinkConfirmForm(), "email": user.email},
        )

    form = MagicLinkConfirmForm(request.POST)
    # login() in confirm() updates last_login, which is what
    # invalidates the single-use token; don't update it twice.
    user = get_user(token, update_last_login=False)
    if user is None or not form.is_valid():
        return _invalid_link(request, next_url)
    user.confirm(
        request,
        auth_user=True,
        remember_user=1 if form.cleaned_data["remember"] else 0,
    )
    messages.success(request, _("Sign-in complete."))
    return _redirect_next(next_url)


def _redirect_next(next_url: str):
    if next_url:
        return HttpResponseRedirect(next_url)
    return redirect("home")


def _invalid_link(request, next_url: str):
    if request.user.is_authenticated:
        # Most likely a double tap on "Sign in", or the link reopened
        # from the email later: nothing has gone wrong for them.
        messages.info(request, _("You're already signed in."))
        return _redirect_next(next_url)
    return render(
        request,
        "authentication/verify_invalid.html",
        {"next": next_url},
        status=400,
    )


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, _("You're signed out."))
    return redirect(settings.LOGOUT_REDIRECT_URL)
