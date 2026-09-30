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
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST
from sesame.utils import get_user

from authentication.email import send_magic_link
from authentication.forms import MagicLinkConfirmForm, MagicLinkRequestForm
from authentication.models import (
    SignInAttempt,
    get_user_by_email,
    normalise_email,
)
from authentication.provisional import (
    ATTEMPT_SESSION_KEY,
    carry_session_choices,
    confirm_session_attempt,
    get_observer,
    start_attempt,
)
from authentication.tokens import attempt_from_token
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


def _too_many_requests(
    request, form, template="authentication/start.html", context=None
):
    form.add_error(
        None,
        _(
            "There have been several requests for links in a short "
            "time. Check your inbox and spam folder for one we've "
            "already sent. If it no longer works, you can ask for a "
            "new one within the hour."
        ),
    )
    return render(request, template, context or {"form": form}, status=429)


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
    # login() in confirm() updates last_login, which is what
    # invalidates the single-use token; don't update it twice.
    try:
        user, attempt = _user_from_link(request, token)
    except AlreadyConfirmed as done:
        return _already_confirmed(request, done.attempt, next_url)
    if request.method != "POST":
        if user is None:
            return _invalid_link(request, next_url)
        return render(
            request,
            "authentication/verify.html",
            {
                "form": MagicLinkConfirmForm(),
                "email": user.email,
                "attempt": attempt,
            },
        )

    form = MagicLinkConfirmForm(request.POST)
    if user is None or not form.is_valid():
        return _invalid_link(request, next_url)
    requested = request.GET.get("lang")
    if not user.preferred_language and requested in dict(settings.LANGUAGES):
        # The language they asked for the link in (§7), stored only now
        # that they've shown the address is theirs (§5.4), and whichever
        # device opens the link.
        user.preferred_language = requested
    with transaction.atomic():
        # Attempt first: it locks the row against a concurrent drop.
        if attempt is not None:
            outcome = attempt.confirm()
            if outcome == SignInAttempt.ALREADY_CONFIRMED:
                return _already_confirmed(request, attempt, next_url)
            if outcome == SignInAttempt.DROPPED:
                return _invalid_link(request, next_url)
        in_this_browser = (
            attempt is not None
            and request.session.get(ATTEMPT_SESSION_KEY) == attempt.pk
        )
        user.confirm(
            request,
            auth_user=True,
            remember_user=1 if form.cleaned_data["remember"] else 0,
        )
        if attempt is None:
            # Signing in the ordinary way proves the address too.
            in_this_browser = confirm_session_attempt(request, user)
        if in_this_browser:
            carry_session_choices(request, user)
    if attempt is not None or in_this_browser:
        messages.success(
            request,
            _(
                "Your email address is confirmed: what you recorded "
                "will count."
            ),
        )
    else:
        messages.success(request, _("Sign-in complete."))
    return _redirect_next(next_url)


class AlreadyConfirmed(Exception):
    def __init__(self, attempt):
        super().__init__()
        self.attempt = attempt


def _already_confirmed(request, attempt, next_url):
    holds_it = request.session.get(ATTEMPT_SESSION_KEY) == attempt.pk
    if holds_it or request.user.is_authenticated:
        messages.info(
            request,
            _(
                "Your email address is already confirmed: what you "
                "recorded counts."
            ),
        )
        return _redirect_next(next_url)
    return render(
        request,
        "authentication/verify_invalid.html",
        {"next": next_url, "already_confirmed": True},
    )


def _user_from_link(request, token):
    """Return (user, attempt) for a magic link, or (None, None).

    A link for a provisional sign-in carries ?attempt=1 and an
    attempt token (authentication.tokens), valid for longer (§5.4)
    and single-use through the attempt's own confirmation.
    """
    if request.GET.get("attempt") is None:
        return get_user(token, update_last_login=False), None
    attempt = attempt_from_token(token)
    if attempt is None:
        return None, None
    if attempt.is_confirmed:
        # Reopened after use (often: confirmed in an email app's own
        # browser, then tapped again elsewhere). Not an error.
        raise AlreadyConfirmed(attempt)
    user = attempt.user
    # The signed token vouched for this user, not an auth backend;
    # later requests are checked by ModelBackend (active users only).
    user.backend = "django.contrib.auth.backends.ModelBackend"
    return user, attempt


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
    observer = get_observer(request)
    return render(
        request,
        "authentication/verify_invalid.html",
        {
            "next": next_url,
            # Offer a fresh link for this browser's own attempt rather
            # than a plain sign-in link.
            "attempt": (
                observer.attempt
                if observer.is_provisional
                and not observer.attempt.is_confirmed
                else None
            ),
        },
        status=400,
    )


@require_http_methods(["GET", "POST"])
def observe(request):
    """Take an email address and let the person start observing now.

    Provisional ("probably signed in") sign-in, design §5.4: the
    confirmation link is emailed at the same time, and what they
    record counts once they confirm.
    """
    template = "authentication/observe.html"
    if request.method != "POST":
        next_url = safe_next_url(request, request.GET.get("next"))
        observer = get_observer(request)
        changing = request.GET.get("change") == "1" and observer.is_provisional
        if observer.is_known and not changing:
            return _redirect_next(next_url)
        form = MagicLinkRequestForm(
            initial={
                "next": next_url,
                "email": observer.email if changing else "",
            }
        )
        return render(request, template, {"form": form, "changing": changing})

    form = MagicLinkRequestForm(request.POST)
    # Correcting a mistyped address (?change=1, carried in the form).
    context = {
        "form": form,
        "changing": request.POST.get("change") == "1"
        and get_observer(request).is_provisional,
    }
    if is_rate_limited(
        "auth_start_ip",
        client_ip(request),
        *settings.RATE_LIMIT_AUTH_START_PER_IP,
    ):
        return _too_many_requests(request, form, template, context)
    if not form.is_valid():
        return render(request, template, context)
    next_url = safe_next_url(request, form.cleaned_data["next"])
    email = normalise_email(form.cleaned_data["email"])
    if form.is_bot():
        logger.warning("Honeypot filled on observe; ignoring")
        return _redirect_next(next_url)
    if is_rate_limited(
        "auth_start_email", email, *settings.RATE_LIMIT_AUTH_START_PER_EMAIL
    ):
        return _too_many_requests(request, form, template, context)
    try:
        attempt = start_attempt(request, email, next_url)
    except (smtplib.SMTPException, OSError):
        # The attempt exists by now; only the email failed, and the
        # banner offers to send it again.
        logger.exception("Failed to send provisional sign-in link")
        messages.warning(
            request,
            _(
                "You can start now, but we couldn't send the email just "
                "now. Use “Send it again” in a few minutes."
            ),
        )
        return _redirect_next(next_url)
    if attempt is None:
        # Deactivated address: say nothing more than for anyone else.
        request.session[SENT_EMAIL_SESSION_KEY] = email
        return redirect("auth_sent")
    if context["changing"]:
        message = _("We've sent a new link to %(email)s.")
    else:
        message = _("You can start now. We've sent a link to %(email)s.")
    messages.success(request, message % {"email": attempt.email})
    return _redirect_next(next_url)


@require_POST
def observe_resend(request):
    """Resend the confirmation link for this browser's attempt."""
    next_url = safe_next_url(request, request.POST.get("next"))
    observer = get_observer(request)
    attempt = observer.attempt
    if attempt is None or attempt.is_confirmed:
        return _redirect_next(next_url)
    if is_rate_limited(
        "auth_start_email",
        attempt.email,
        *settings.RATE_LIMIT_AUTH_START_PER_EMAIL,
    ):
        messages.warning(
            request,
            _(
                "We've sent several links already. Check your inbox and "
                "spam folder, or try again later."
            ),
        )
        return _redirect_next(next_url)
    try:
        send_magic_link(request, attempt.user, next_url, attempt=attempt)
    except (smtplib.SMTPException, OSError):
        logger.exception("Failed to resend provisional sign-in link")
        messages.warning(
            request,
            _("We couldn't send the email just now. Please try again soon."),
        )
        return _redirect_next(next_url)
    messages.success(
        request,
        _("We've sent the link again to %(email)s.")
        % {"email": attempt.email},
    )
    return _redirect_next(next_url)


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, _("You're signed out."))
    return redirect(settings.LOGOUT_REDIRECT_URL)
