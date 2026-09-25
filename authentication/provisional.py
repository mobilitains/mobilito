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

# Provisional ("probably signed in") sign-in, design §5.3/§5.4.
#
# Views that create or show someone's own observations work with an
# Observer rather than request.user: either a real signed-in user,
# or a provisional sign-in attempt held in the browser session. The
# attempt is deliberately not a Django login, so it can never see
# or change anything belonging to an existing user with the same
# email address.

import functools
import logging
from dataclasses import dataclass
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django_htmx.http import HttpResponseClientRedirect

from authentication.email import send_magic_link
from authentication.models import (
    SignInAttempt,
    get_user_by_email,
    lock_user_first,
)

logger = logging.getLogger(__name__)

ATTEMPT_SESSION_KEY = "sign_in_attempt_id"


@dataclass(frozen=True)
class Observer:
    """Whoever is making observations in this request."""

    user: object = None  # a signed-in MobilitoUser
    attempt: SignInAttempt | None = None

    @property
    def is_known(self) -> bool:
        return self.user is not None or self.attempt is not None

    @property
    def is_provisional(self) -> bool:
        return self.user is None and self.attempt is not None

    @property
    def email(self) -> str:
        if self.user is not None:
            return self.user.email
        return self.attempt.email if self.attempt else ""

    def owner_fields(self) -> dict:
        """Ownership fields for a new observation."""
        if self.user is not None:
            return {"user": self.user}
        if self.attempt.is_confirmed:
            # Confirmed from another browser (often an email app's
            # own): the address is proven for this browser's attempt,
            # so what it records now belongs to that user too.
            return {"user": self.attempt.user, "sign_in_attempt": self.attempt}
        return {"user": None, "sign_in_attempt": self.attempt}

    def owns(self, observation) -> bool:
        if self.user is not None and observation.user_id == self.user.pk:
            return True
        return (
            self.attempt is not None
            and observation.sign_in_attempt_id == self.attempt.pk
        )

    def own(self, queryset):
        """Restrict an observation queryset to this observer's own."""
        condition = Q(pk__in=[])
        if self.user is not None:
            condition |= Q(user=self.user)
        if self.attempt is not None:
            condition |= Q(sign_in_attempt=self.attempt)
        return queryset.filter(condition)


def get_observer(request) -> Observer:
    """The request's Observer, computed once per request."""
    cached = getattr(request, "_observer", None)
    if cached is not None and cached[0] == (
        request.user.pk,
        request.session.get(ATTEMPT_SESSION_KEY),
    ):
        return cached[1]
    observer = _compute_observer(request)
    request._observer = (
        (request.user.pk, request.session.get(ATTEMPT_SESSION_KEY)),
        observer,
    )
    return observer


def _compute_observer(request) -> Observer:
    attempt = None
    attempt_id = request.session.get(ATTEMPT_SESSION_KEY)
    if attempt_id is not None:
        attempt = (
            SignInAttempt.objects.select_related("user")
            .filter(pk=attempt_id)
            .first()
        )
        if attempt is not None and (
            not attempt.user.is_active or _confirmed_long_ago(attempt)
        ):
            # A deactivated user's attempt carries nothing; and a
            # confirmed attempt only stands in for signing in for a
            # while (§5.4), after which this browser signs in properly.
            request.session.pop(ATTEMPT_SESSION_KEY, None)
            attempt = None
        elif attempt is None:
            # Dropped (never confirmed) since: say so, once.
            request.session.pop(ATTEMPT_SESSION_KEY, None)
            messages.info(
                request,
                _(
                    "Your email address wasn't confirmed in time, so "
                    "anything you recorded was deleted."
                ),
            )
    if request.user.is_authenticated:
        if attempt is not None and attempt.user_id != request.user.pk:
            attempt = None
        return Observer(user=request.user, attempt=attempt)
    return Observer(attempt=attempt)


def _confirmed_long_ago(attempt) -> bool:
    if attempt.confirmed_at is None:
        return False
    window = settings.SIGN_IN_ATTEMPT_CONFIRMED_SESSION_HOURS * 3600
    return (timezone.now() - attempt.confirmed_at).total_seconds() > window


def observer_required(view):
    """Send people with no identity at all to give an email first."""

    @functools.wraps(view)
    def inner(request, *args, **kwargs):
        request.observer = get_observer(request)
        if request.observer.is_known:
            return view(request, *args, **kwargs)
        target = (
            reverse("auth_observe")
            + "?"
            + urlencode({"next": request.get_full_path()})
        )
        if request.htmx:
            return HttpResponseClientRedirect(target)
        return redirect(target)

    return inner


def start_attempt(request, email: str, next_url: str = ""):
    """Begin a provisional sign-in and email its confirmation link.

    Returns the attempt, or None if this address can't sign in
    (a deactivated user), in which case nothing is sent.

    If this browser already holds an unconfirmed attempt (the
    address was mistyped), what it recorded moves to the new
    attempt, and the old one goes.
    """
    from authentication.models import MobilitoUser, normalise_email

    existed = MobilitoUser.objects.filter(
        email=normalise_email(email)
    ).exists()
    user = get_user_by_email(email)
    if not user.is_active:
        return None
    previous = get_observer(request).attempt
    with transaction.atomic():
        attempt = SignInAttempt.objects.create(
            email=user.email,
            user=user,
            created_user=not existed,
            language=translation.get_language() or "",
        )
        if previous is not None:
            # Locked (user row first) and re-read: if it was confirmed
            # elsewhere in the meantime, its records are that user's
            # and stay put.
            lock_user_first(previous.user_id)
            previous = (
                SignInAttempt.objects.select_for_update()
                .filter(pk=previous.pk, confirmed_at__isnull=True)
                .first()
            )
        if previous is not None:
            from core.lifecycle import observations_for_attempt

            for queryset in observations_for_attempt(previous):
                queryset.filter(user__isnull=True).update(
                    sign_in_attempt=attempt
                )
            _delete_attempt(previous)
    request.session[ATTEMPT_SESSION_KEY] = attempt.pk
    send_magic_link(request, user, next_url, attempt=attempt)
    return attempt


def carry_session_choices(request, user) -> None:
    """Keep choices made while provisional, now the address is proven.

    Until then they lived only in the session (§5.4).
    """
    from core.maps import DEVICE_LOCATION_SESSION_KEY

    if DEVICE_LOCATION_SESSION_KEY in request.session:
        user.use_device_location = request.session[DEVICE_LOCATION_SESSION_KEY]
        user.save(update_fields=["use_device_location"])


def confirm_session_attempt(request, user) -> bool:
    """Confirm this browser's attempt once `user` has proved the address.

    Used when someone holding a provisional sign-in signs in the
    ordinary way (a plain magic link) instead of using the attempt's
    own link: that proves the address just as well. Only the attempt
    held by this browser is confirmed, never other attempts made
    with the same address (possibly by someone else).
    """
    attempt_id = request.session.get(ATTEMPT_SESSION_KEY)
    attempt = SignInAttempt.objects.filter(
        pk=attempt_id, user=user, confirmed_at__isnull=True
    ).first()
    if attempt is None:
        return False
    return attempt.confirm() == SignInAttempt.CONFIRMED


def drop_attempt(attempt: SignInAttempt) -> bool:
    """Delete everything created during an unconfirmed attempt (§5.4).

    Observations (with their count events, location evidence,
    moderation flags and stored photos), locations left unused, the
    attempt itself, and the user if this attempt created them and
    they never confirmed or did anything else.

    Re-reads and locks the attempt first: `attempt` may be stale (the
    reminder command loads attempts, then sends email for a while),
    and one confirmed meanwhile must be left alone. Returns whether
    anything was dropped.
    """
    from core.lifecycle import observations_for_attempt
    from core.models import Location

    doomed_files = []
    with transaction.atomic():
        lock_user_first(attempt.user_id)
        attempt = (
            SignInAttempt.objects.select_for_update()
            .filter(pk=attempt.pk, confirmed_at__isnull=True)
            .first()
        )
        if attempt is None:
            return False
        location_ids = set()
        for queryset in observations_for_attempt(attempt):
            # Never anything already attached to a user.
            for observation in queryset.filter(user__isnull=True):
                location_ids.add(observation.location_id)
                media = getattr(observation, "media", None)
                if media is not None:
                    for item in media.all():
                        item.moderation_flags.all().delete()
                        if item.file:
                            doomed_files.append(
                                (item.file.storage, item.file.name)
                            )
                observation.delete()
        for location in Location.objects.filter(pk__in=location_ids):
            if not any(
                getattr(location, f"{model._meta.model_name}s").exists()
                for model in _observation_models()
            ):
                location.delete()
        # Anything already attached to a user outlives the attempt.
        for queryset in observations_for_attempt(attempt):
            queryset.filter(user__isnull=False).update(sign_in_attempt=None)
        _delete_attempt(attempt)

        def delete_files():
            # Only once the rows are gone for good.
            for storage, name in doomed_files:
                storage.delete(name)

        transaction.on_commit(delete_files)
    logger.info("Dropped unconfirmed sign-in attempt")
    return True


def _delete_attempt(attempt: SignInAttempt) -> None:
    """Delete an unconfirmed attempt, and its user if it made one.

    Call inside a transaction, with the attempt's observations
    already moved or deleted. If other unconfirmed attempts share a
    user this attempt created, the responsibility passes to them;
    otherwise the user goes if nothing else ever happened to them.
    """
    from django.contrib.auth import get_user_model

    created_user, user_id = attempt.created_user, attempt.user_id
    attempt.delete()
    if not created_user:
        return
    user = (
        get_user_model().objects.select_for_update().filter(pk=user_id).first()
    )
    if user is None:
        return
    others = user.sign_in_attempts.filter(confirmed_at__isnull=True)
    if others.exists():
        others.update(created_user=True)
    elif _user_is_disposable(user):
        user.delete()


def _observation_models():
    from core.lifecycle import observation_models

    return observation_models()


def _user_is_disposable(user) -> bool:
    """A user record that exists only because of dropped attempts."""
    if (
        user.email_validated
        or user.is_staff
        or user.is_superuser
        or user.last_login is not None
        # create_user() leaves password empty, which Django still
        # counts as "usable"; only a real password hash matters here.
        or (bool(user.password) and user.has_usable_password())
        or user.sign_in_attempts.exists()
    ):
        return False
    return not any(
        model.objects.filter(user=user).exists()
        for model in _observation_models()
    )
