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

from core.models import ModerationState, Observation


class Mode(models.TextChoices):
    PEDESTRIAN = "ped", _("Pedestrian")
    CYCLIST = "bike", _("Cyclist")
    CAR = "car", _("Car")
    TC = "tc", _("Public transit")


class ModalShareSession(Observation):
    """A timed field count of passing vehicles by mode (§9.1, §20.3)."""

    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    # Totals recorded at finish, for integrity cross-check against the
    # ModalShareCountEvent stream (§20.3).
    total_pedestrian = models.PositiveIntegerField(default=0)
    total_cyclist = models.PositiveIntegerField(default=0)
    total_car = models.PositiveIntegerField(default=0)
    total_tc = models.PositiveIntegerField(default=0)
    integrity_hash = models.CharField(max_length=64, blank=True)
    location_mismatch = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"Modal share session {self.pk} at {self.location}"


class ModalShareCountEvent(models.Model):
    """A single timestamped tap during a modal share session (§20.4)."""

    session = models.ForeignKey(
        ModalShareSession,
        on_delete=models.CASCADE,
        related_name="events",
    )
    timestamp = models.DateTimeField()
    mode = models.CharField(max_length=4, choices=Mode.choices)
    point = gis_models.PointField(geography=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.get_mode_display()} tap in session {self.session_id}"

    class Meta:
        indexes = [models.Index(fields=["session", "timestamp"])]


class ObserverPerspective(models.TextChoices):
    PEDESTRIAN = "ped", _("Pedestrian")
    CYCLIST = "bike", _("Cyclist")
    BOTH = "both", _("Both")


class InfrastructureObservation(Observation):
    """A report on active mobility infrastructure (§9.2, §20.5)."""

    observer_perspective = models.CharField(
        max_length=4, choices=ObserverPerspective.choices
    )
    description = models.TextField(blank=True)
    moderation_state = models.CharField(
        max_length=20,
        choices=ModerationState.choices,
        default=ModerationState.UNREVIEWED,
    )

    def __str__(self) -> str:
        return f"Infrastructure observation {self.pk} at {self.location}"


class MediaType(models.TextChoices):
    # Only photo is usable in v1; video is deferred to v2 (§22.3).
    PHOTO = "photo", _("Photo")


class InfrastructureMedia(models.Model):
    """A photo attached to an infrastructure observation (§20.6)."""

    observation = models.ForeignKey(
        InfrastructureObservation,
        on_delete=models.CASCADE,
        related_name="media",
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="infrastructure_media",
    )
    media_type = models.CharField(
        max_length=10,
        choices=MediaType.choices,
        default=MediaType.PHOTO,
    )
    file = models.ImageField(upload_to="infrastructure_media/%Y/%m/")
    exif_point = gis_models.PointField(geography=True, null=True, blank=True)
    moderation_state = models.CharField(
        max_length=20,
        choices=ModerationState.choices,
        default=ModerationState.UNREVIEWED,
    )
    published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Media {self.pk} for observation {self.observation_id}"


class TagStatus(models.TextChoices):
    ACTIVE = "active", _("Active")
    DEPRECATED = "deprecated", _("Deprecated")


class InfrastructureTag(models.Model):
    """An ontology keyword applicable to infrastructure observations.

    label and description are translated per-locale via
    django-modeltranslation; see translation.py (§10, §20.7).
    """

    label = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    country = models.CharField(
        max_length=2,
        blank=True,
        help_text=_("ISO 3166-1 alpha-2 code. Blank means universal."),
    )
    family = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=20,
        choices=TagStatus.choices,
        default=TagStatus.ACTIVE,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.label


class ActionType(models.TextChoices):
    ME_TOO = "me_too", _("Me too")
    PROGRESS = "progress", _("Progress")
    DEGRADATION = "degradation", _("Degradation")
    RESOLVED = "resolved", _("Resolved")
    ADDITIONAL_ISSUE = "additional_issue", _("Additional issue")


class ObservationAction(models.Model):
    """A me-too, update, or resolution note on an observation (§20.8)."""

    observation = models.ForeignKey(
        InfrastructureObservation,
        on_delete=models.CASCADE,
        related_name="actions",
    )
    action_type = models.CharField(max_length=20, choices=ActionType.choices)
    text = models.TextField(blank=True)
    media = models.ForeignKey(
        InfrastructureMedia,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="observation_actions",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="observation_actions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # Set when a me-too is cancelled within the cancellation window.
    cancelled_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        action = self.get_action_type_display()
        return f"{action} on observation {self.observation_id}"


class ContactType(models.TextChoices):
    EMAIL = "email", _("Email")


class ContactMethod(models.Model):
    """A mailable authority contact for a geographic zone (§12.2, §20.9).

    `user` is an optional link to a platform user, unused in v1 but
    kept so the future authority-as-user model (§12.2, v2+) does not
    require a schema migration.
    """

    country = models.CharField(max_length=2, blank=True)
    region = models.CharField(max_length=100, blank=True)
    department = models.CharField(max_length=100, blank=True)
    commune = models.CharField(max_length=100, blank=True)
    contact_type = models.CharField(
        max_length=20,
        choices=ContactType.choices,
        default=ContactType.EMAIL,
    )
    contact_value = models.CharField(max_length=255)
    do_not_contact = models.BooleanField(default=False)
    display_guidance = models.TextField(blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contact_methods",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        zone = self.commune or self.department or self.country
        return f"{self.contact_value} ({zone})"


class FlagReason(models.TextChoices):
    INAPPROPRIATE = "inappropriate", _("Inappropriate")
    SPAM = "spam", _("Spam")
    OFF_TOPIC = "off_topic", _("Off-topic")
    OTHER = "other", _("Other")


class ModerationFlag(models.Model):
    """A user-initiated flag on a photo or description text (§13.4).

    Attaches generically to whatever content type is flagged (e.g. an
    InfrastructureObservation or an InfrastructureMedia item).
    """

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="moderation_flags",
    )
    reason = models.CharField(
        max_length=20,
        choices=FlagReason.choices,
        default=FlagReason.OTHER,
    )
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Flag ({self.get_reason_display()}) on {self.target}"

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id"])]
