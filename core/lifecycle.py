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

# Publication lifecycle shared by both observation types (§14):
# where a submission goes, promotion on email validation, and the
# moderation transitions (roadmap Phase 8; admin actions for now,
# the moderation dashboard in Phase 13).

import uuid

from django.apps import apps
from django.core.cache import cache
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from core.models import ModerationState, PublicationState

OBSERVATION_MODELS = (
    "mobilito_app.ModalShareSession",
    "mobilito_app.InfrastructureObservation",
)


def observation_models():
    return [apps.get_model(label) for label in OBSERVATION_MODELS]


def observations_for_attempt(attempt):
    return [
        model.objects.filter(sign_in_attempt=attempt)
        for model in observation_models()
    ]


def submission_state(user) -> str:
    """Where a just-submitted observation goes (§14).

    Validated users' observations queue for moderation; anything
    else (no user yet, i.e. a provisional sign-in, or an unvalidated
    user) waits for validation and is never shown to others.
    """
    if user is not None and user.email_validated:
        return PublicationState.PENDING_MODERATION
    return PublicationState.PENDING_VALIDATION


def promote(user) -> None:
    """Move a newly validated user's waiting observations on (§14)."""
    if not user.email_validated:
        return
    for model in observation_models():
        model.objects.filter(
            user=user,
            publication_state=PublicationState.PENDING_VALIDATION,
        ).update(publication_state=PublicationState.PENDING_MODERATION)


# Reachable by anyone with the link (§13.2): published, or on light
# hold (then with a note that it is being reviewed, and off the map
# and list). Everything else only its author and moderators see.
LINK_VISIBLE_STATES = (
    PublicationState.PUBLISHED,
    PublicationState.LIGHT_HOLD,
)

# What a moderator can move an observation to.
MODERATION_TARGETS = (
    PublicationState.PUBLISHED,
    PublicationState.LIGHT_HOLD,
    PublicationState.SANDBOXED,
)


class TransitionError(ValueError):
    """A moderation transition that isn't allowed; str() says why."""


def publish_blocker(observation):
    """Why this observation can't be published, or None if it can.

    Never publish what its author hasn't validated (CLAUDE.md: "must
    not be shown to others"), nor what isn't finished.
    """
    if observation.publication_state == PublicationState.DRAFT:
        return _("It hasn't been sent yet.")
    if getattr(observation, "finished_at", True) is None:
        return _("The count isn't finished.")
    user = observation.user
    if user is None or not user.email_validated:
        return _("Its author hasn't confirmed their email address yet.")
    return None


def moderate(observation, target: str) -> bool:
    """Move an observation to a moderation outcome (§13.2, §14).

    Returns False if it was already there. Raises TransitionError if
    the move isn't allowed. Published and light hold are both seen
    by others (light hold by link), so both need what publishing
    needs; sandboxing hides, so it's allowed from any sent state.
    Drafts (counts still going) aren't moderated: nobody else can
    see them, and finishing one would overwrite the outcome.
    """
    if target not in MODERATION_TARGETS:
        raise TransitionError(_("Not a moderation outcome."))
    if observation.publication_state == target:
        return False
    if target in LINK_VISIBLE_STATES:
        reason = publish_blocker(observation)
        if reason is not None:
            raise TransitionError(reason)
    elif observation.publication_state == PublicationState.DRAFT:
        raise TransitionError(_("It hasn't been sent yet."))
    observation.publication_state = target
    fields = ["publication_state", "updated_at"]
    if hasattr(observation, "moderation_state"):
        observation.moderation_state = (
            ModerationState.CLEARED
            if target == PublicationState.PUBLISHED
            else ModerationState.FLAGGED
        )
        fields.append("moderation_state")
    observation.save(update_fields=fields)
    transaction.on_commit(public_content_changed)
    return True


PUBLIC_GENERATION_KEY = "public:generation"


def public_generation() -> str:
    """Changes whenever what the public sees changes by moderation.

    Part of cache keys for public data (map pins), so publishing or
    hiding something shows at once rather than when entries expire.
    Random rather than a counter, so an evicted key can't bring back
    old entries. Needs a cache shared by all workers (core.W001).
    """
    return cache.get_or_set(
        PUBLIC_GENERATION_KEY, lambda: uuid.uuid4().hex[:12], timeout=None
    )


def public_content_changed() -> None:
    cache.set(PUBLIC_GENERATION_KEY, uuid.uuid4().hex[:12], timeout=None)
