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

# Publication lifecycle helpers shared by both observation types
# (§14). Phase 8 adds moderation transitions; this covers what
# submission and email validation need.

from django.apps import apps

from core.models import PublicationState

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
