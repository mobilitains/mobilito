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

# Who may see an observation, and user-initiated flagging (design
# §13.2, §13.4; roadmap Phase 8). Moderation itself happens in the
# Django admin for v1-preview (mobilito_app/admin.py).
#
# /flag/<kind>/<id>/  "Report a problem" with a report's text, one
#                     of its photos, or a count's place name

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from authentication.provisional import get_observer
from core.lifecycle import LINK_VISIBLE_STATES, moderate
from core.models import ModerationState, PublicationState
from core.ratelimit import client_ip, is_rate_limited
from mobilito_app.forms import FlagForm
from mobilito_app.models import (
    InfrastructureMedia,
    InfrastructureObservation,
    ModalShareSession,
    ModerationFlag,
)

logger = logging.getLogger(__name__)

# Who is looking at an observation.
OWNER = "owner"
MODERATOR = "moderator"
PUBLIC = "public"


def is_moderator(request, model) -> bool:
    """Staff allowed to view this kind of observation in the admin."""
    user = request.user
    opts = model._meta
    return (
        user.is_active
        and user.is_staff
        and user.has_perm(f"{opts.app_label}.view_{opts.model_name}")
    )


def viewer_role(request, observation):
    """OWNER, MODERATOR, PUBLIC, or None if they may not see it.

    Authors always see their own; moderators see everything (§13.2:
    sandboxed is "author and admin only"); anyone else sees what is
    published or on light hold.
    """
    if get_observer(request).owns(observation):
        return OWNER
    if is_moderator(request, type(observation)):
        return MODERATOR
    # The author check again, though moderation already makes it: an
    # author whose confirmation was withdrawn is hidden too.
    user = observation.user
    if (
        observation.publication_state in LINK_VISIBLE_STATES
        and user is not None
        and user.email_validated
    ):
        return PUBLIC
    return None


def visible_or_404(request, queryset, pk):
    """(observation, role) if this request may see it, else 404."""
    observation = get_object_or_404(
        queryset.select_related("location", "user"), pk=pk
    )
    role = viewer_role(request, observation)
    if role is None:
        raise Http404
    return observation, role


def visible_media(report, role):
    """The photos this viewer may see: authors and moderators all."""
    media = report.media.all()
    if role == PUBLIC:
        media = media.filter(published=True)
    return media


def state_context(observation, role) -> dict:
    """What detail pages need to say about where an observation is."""
    state = observation.publication_state
    return {
        "role": role,
        "is_owner": role == OWNER,
        "is_moderator": role == MODERATOR,
        "published": state == PublicationState.PUBLISHED,
        "on_hold": state == PublicationState.LIGHT_HOLD,
        "sandboxed": state == PublicationState.SANDBOXED,
        "pending_validation": state == PublicationState.PENDING_VALIDATION,
        # Admin-only wording (§14): moderators may see state names.
        "state_label": observation.get_publication_state_display(),
        "admin_url": reverse(
            "admin:%s_%s_change"
            % (observation._meta.app_label, observation._meta.model_name),
            args=[observation.pk],
        ),
        # Others' content can be flagged; your own you can't.
        "can_flag": role == PUBLIC,
        "contact_email": settings.CONTACT_EMAIL,
    }


def _flag_target(request, kind, pk):
    """(target, the observation it's part of) if it may be flagged.

    kind is "report" (its text), "photo" or "count" (its place name,
    the only thing in a count someone typed).
    """
    if kind == "report":
        report, role = visible_or_404(
            request, InfrastructureObservation.objects, pk
        )
        target, observation = report, report
        if not (report.description or report.location.user_entered_address):
            role = None  # no words of anyone's to flag
    elif kind == "photo":
        item = get_object_or_404(
            InfrastructureMedia.objects.select_related(
                "observation__user", "observation__location"
            ),
            pk=pk,
        )
        observation = item.observation
        role = viewer_role(request, observation)
        if role == PUBLIC and not item.published:
            role = None
        target = item
    elif kind == "count":
        observation, role = visible_or_404(
            request, ModalShareSession.objects, pk
        )
        target = observation
        # Only a typed place name is someone's words; nothing else in
        # a count can be offensive.
        if (
            observation.is_open
            or not observation.location.user_entered_address
        ):
            role = None
    else:
        raise Http404
    if role != PUBLIC:
        # Not visible, or your own (or a moderator's to act on).
        raise Http404
    return target, observation


def _already_flagged(request, kind, target) -> bool:
    if request.user.is_authenticated:
        return ModerationFlag.objects.filter(
            content_type=ContentType.objects.get_for_model(target),
            object_id=target.pk,
            reporter=request.user,
            resolved_at__isnull=True,
        ).exists()
    return f"{kind}:{target.pk}" in request.session.get("flagged", [])


def _remember_flag(request, kind, target) -> None:
    if not request.user.is_authenticated:
        flagged = request.session.get("flagged", [])
        # Bounded: only for not saying thank-you twice.
        request.session["flagged"] = (flagged + [f"{kind}:{target.pk}"])[-100:]


def resolve_flags(target, user) -> int:
    """Mark a target's open flags reviewed; returns how many."""
    return ModerationFlag.objects.filter(
        content_type=ContentType.objects.get_for_model(target),
        object_id=target.pk,
        resolved_at__isnull=True,
    ).update(resolved_at=timezone.now(), resolved_by=user)


def _open_reporters(target) -> int:
    """Distinct confirmed users with an open flag on this target."""
    return (
        ModerationFlag.objects.filter(
            content_type=ContentType.objects.get_for_model(target),
            object_id=target.pk,
            resolved_at__isnull=True,
            reporter__email_validated=True,
        )
        .values("reporter")
        .distinct()
        .count()
    )


def auto_hold(target, observation) -> bool:
    """Hide content flagged by enough people until reviewed (§13.4).

    Only flags from people who confirmed their email count, so a
    visitor opening fresh sessions can't hide others' content. A
    photo is hidden on its own; a report's text or a count's place
    name puts the observation on light hold (still reachable by its
    link, off the map and list). Returns True if anything changed.
    """
    threshold = settings.MODERATION_FLAG_AUTO_HOLD_REPORTERS
    if not threshold or _open_reporters(target) < threshold:
        return False
    if isinstance(target, InfrastructureMedia):
        if not target.published:
            return False
        target.published = False
        target.moderation_state = ModerationState.FLAGGED
        target.save(update_fields=["published", "moderation_state"])
        logger.warning("Photo %s hidden after repeated flags", target.pk)
        return True
    if observation.publication_state != PublicationState.PUBLISHED:
        return False
    moderate(observation, PublicationState.LIGHT_HOLD)
    logger.warning("%s put on light hold after repeated flags", observation)
    return True


@require_http_methods(["GET", "POST"])
def flag(request, kind, pk):
    """Report a problem with something someone else posted (§13.4).

    Works without JavaScript (a page of its own, then back to the
    observation); with htmx, the form opens in place and is replaced
    by the thank-you.
    """
    target, observation = _flag_target(request, kind, pk)
    template = (
        "mobilito_app/moderation/flag_form.html"
        if request.htmx
        else "mobilito_app/moderation/flag.html"
    )
    location = observation.location
    context = {
        "kind": kind,
        # The words being reported, shown back so it's clear what.
        "excerpt": (
            ""
            if kind == "photo"
            else " — ".join(
                text
                for text in (
                    location.user_entered_address,
                    getattr(target, "description", ""),
                )
                if text
            )
        ),
        "target": target,
        "observation": observation,
        "back_url": observation.get_absolute_url(),
        "action_url": reverse("flag", args=[kind, pk]),
    }
    if _already_flagged(request, kind, target):
        return render(request, template, {**context, "already": True})
    if request.method == "GET":
        return render(
            request, template, {**context, "form": FlagForm(kind=kind)}
        )

    form = FlagForm(request.POST, kind=kind)
    if not form.is_valid():
        # htmx doesn't swap in error responses: show the errors in
        # place with a 200.
        return render(
            request,
            template,
            {**context, "form": form},
            status=200 if request.htmx else 400,
        )
    if form.is_bot():
        logger.warning("Honeypot filled on flag; ignoring")
    elif is_rate_limited(
        "flag", client_ip(request), *settings.RATE_LIMIT_FLAG
    ):
        # Say thank you anyway: nothing useful to tell a flood.
        logger.warning("Flag rate limit hit")
    else:
        ModerationFlag.objects.create(
            target=target,
            reporter=(request.user if request.user.is_authenticated else None),
            reason=form.cleaned_data["reason"],
            note=form.cleaned_data["note"],
        )
        _remember_flag(request, kind, target)
        auto_hold(target, observation)
    if request.htmx:
        return render(request, template, {**context, "thanks": True})
    messages.success(
        request,
        _("Thank you. We'll take a look at what you reported."),
    )
    return redirect(observation.get_absolute_url())
