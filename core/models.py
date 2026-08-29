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
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.contrib.gis.db import models as gis_models
from django.db import models
from django.utils.translation import gettext_lazy as _


class PublicationState(models.TextChoices):
    """Shared publication lifecycle for both observation types (§14).

    Names are for developer/admin use only; users see plain-language
    copy, never these state labels.
    """

    DRAFT = "draft", _("Draft")
    SUBMITTED = "submitted", _("Submitted")
    PENDING_VALIDATION = "pending_validation", _("Pending validation")
    PENDING_MODERATION = "pending_moderation", _("Pending moderation")
    PUBLISHED = "published", _("Published")
    LIGHT_HOLD = "light_hold", _("Light hold")
    SANDBOXED = "sandboxed", _("Sandboxed")


class ModerationState(models.TextChoices):
    """Outcome of content moderation, distinct from publication state.

    Publication state tracks where an observation is in the
    lifecycle; moderation state records the review outcome once
    assessed (manually for now; by the LLM pipeline from Phase 12
    onward, §13.1).
    """

    UNREVIEWED = "unreviewed", _("Unreviewed")
    CLEARED = "cleared", _("Cleared")
    FLAGGED = "flagged", _("Flagged for review")


class Observation(models.Model):
    """Fields shared by both observation types (§14, §20.3, §20.5).

    Abstract: ModalShareSession and InfrastructureObservation each get
    their own table and add type-specific fields on top of these.
    """

    # SET_NULL (not CASCADE): the GDPR deletion flow (§5.5, Phase 15)
    # anonymises the user record rather than deleting it, so this
    # should never fire in normal operation -- it's a safety net
    # against observation content being silently destroyed if a user
    # row is ever hard-deleted some other way (e.g. Django admin's
    # stock delete action), matching ObservationAction.created_by and
    # InfrastructureMedia.added_by.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="%(class)ss",
    )
    location = models.ForeignKey(
        "core.Location",
        on_delete=models.PROTECT,
        related_name="%(class)ss",
    )
    publication_state = models.CharField(
        max_length=20,
        choices=PublicationState.choices,
        default=PublicationState.DRAFT,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Location(models.Model):
    """A geographic point where one or more observations were made.

    Location equivalence radii (§11.4) live in Django settings rather
    than on this model, since they vary by observation type rather
    than by location.
    """

    point = gis_models.PointField(geography=True)
    user_entered_address = models.CharField(max_length=255, blank=True)
    reverse_geocoded_address = models.CharField(max_length=255, blank=True)
    country = models.CharField(max_length=100, blank=True)
    region = models.CharField(max_length=100, blank=True)
    department = models.CharField(max_length=100, blank=True)
    commune = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return (
            self.user_entered_address
            or self.reverse_geocoded_address
            or f"Location {self.pk}"
        )


class LocationEvidence(models.Model):
    """Geolocation evidence gathered for one observation (§11.1, §20.10).

    Attaches generically since an observation may be a
    ModalShareSession or an InfrastructureObservation.
    """

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    observation = GenericForeignKey("content_type", "object_id")

    device_point = gis_models.PointField(geography=True, null=True, blank=True)
    user_adjusted_point = gis_models.PointField(
        geography=True, null=True, blank=True
    )
    edge_point = gis_models.PointField(geography=True, null=True, blank=True)
    exif_point = gis_models.PointField(geography=True, null=True, blank=True)
    accuracy_metres = models.FloatField(null=True, blank=True)
    timestamp = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Location evidence {self.pk} for {self.observation}"

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id"])]
