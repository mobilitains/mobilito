"""
Copyright 2024  Francais pour une Meilleure Mobilité.

Author(s): Jeff Abrahamson <jeff@p27.eu>.

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

import logging

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.core.handlers.wsgi import WSGIRequest
from django.db import DatabaseError, IntegrityError, models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger("django")


def normalise_email(email: str) -> str:
    return email.lower()


class MobilitoUserManager(BaseUserManager):
    """Manage user creation.

    create_user() is idempotent: if the address already exists it
    returns the existing user rather than raising an error.  This
    matches the magic-link auth flow where the user may not know
    whether they have previously authenticated.
    """

    def create_user(self, email: str) -> "MobilitoUser":
        email = normalise_email(email)
        with transaction.atomic():
            try:
                user = self.create(email=email)
                logger.info("Created user")
                return user
            except IntegrityError:
                pass
        user = self.get(email=email)
        logger.info("Returned existing user")
        return user

    def create_superuser(
        self, email: str, password: str = None, **extra_fields
    ) -> "MobilitoUser":
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if not extra_fields["is_staff"]:
            raise ValueError(_("Superuser must have is_staff=True."))
        if not extra_fields["is_superuser"]:
            raise ValueError(_("Superuser must have is_superuser=True."))
        user = self.create_user(email)
        for key, value in extra_fields.items():
            setattr(user, key, value)
        if password:
            user.set_password(password)
        user.save(using=self._db)
        return user

    def get_by_natural_key(self, email: str) -> "MobilitoUser":
        return self.get(email=normalise_email(email))


class MobilitoUser(AbstractBaseUser, PermissionsMixin):
    """Custom user model using email as the unique identifier.

    Users are publicly pseudonymous: no name is stored or exposed.
    The UI uses "you/your" and never uses the word "account".

    email_validated tracks whether the user has confirmed control of
    their email address (by clicking a magic link or equivalent).
    Observations from unvalidated users are hidden from other users.
    """

    email = models.EmailField("email address", unique=True)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    email_validated = models.BooleanField(default=False)
    # null is deliberate here (contra DJ01): it distinguishes "no
    # stored preference, fall back to Accept-Language" (§7) from any
    # valid empty value.
    preferred_language = models.CharField(  # noqa: DJ01
        max_length=10,
        choices=settings.LANGUAGES,
        null=True,
        blank=True,
    )
    # Server-persisted geolocation preference (§11.2). Checked by
    # default; when off, the app never calls the browser geolocation
    # API and no permission prompt appears.
    use_device_location = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    EMAIL_FIELD = "email"

    objects = MobilitoUserManager()

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"

    def __str__(self) -> str:
        return self.email

    def __repr__(self) -> str:
        return f"MobilitoUser(id={self.id})"

    def confirm(
        self,
        request: WSGIRequest,
        auth_user: bool = False,
        remember_user: int = 0,
    ) -> None:
        """Mark email as validated and optionally log the user in."""
        from core.lifecycle import promote

        self.email_validated = True
        self.save()
        promote(self)
        if auth_user:
            login(request, self)
        # remember_user=0: expire session when browser closes.
        request.session.set_expiry(0 if remember_user == 0 else None)


def get_user_by_email(email: str) -> MobilitoUser:
    """Return the user for this email, creating one if necessary."""
    email = normalise_email(email)
    try:
        return MobilitoUser.objects.get(email=email)
    except MobilitoUser.DoesNotExist:
        return MobilitoUser.objects.create_user(email)
    except DatabaseError as err:
        logger.error(f"Unexpected database error: {err}")
        raise


def lock_user_first(user_id) -> None:
    """Lock a user row before any of its sign-in attempt rows.

    Confirming, dropping and re-pointing attempts all touch both the
    user and attempt rows; taking them in one fixed order (user, then
    attempt) keeps them from deadlocking each other. Call inside a
    transaction.
    """
    MobilitoUser.objects.select_for_update().filter(pk=user_id).first()


class SignInAttempt(models.Model):
    """A provisional ("probably signed in") sign-in (§5.3, §5.4).

    Created when someone gives an email address in order to start
    observing straight away. The browser session holds the attempt,
    not a Django login: the session sees only what the attempt
    itself created (observations link to it, with no user until it
    is confirmed), so typing someone else's address reveals nothing
    of theirs. Confirming the emailed link attaches the attempt's
    data to `user`; an attempt never confirmed is reminded and then
    dropped with everything linked to it (process_sign_in_attempts).
    """

    email = models.EmailField()
    # The user the confirmation link signs in (created on demand, so
    # possibly never validated).
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sign_in_attempts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # Whether this attempt created `user`, which a drop may then
    # delete; a pre-existing user is never deleted by a drop.
    created_user = models.BooleanField(default=False)
    # Language the person asked in, for reminders sent from cron to a
    # user with no stored preference.
    language = models.CharField(max_length=10, blank=True)
    reminders_sent = models.PositiveSmallIntegerField(default=0)
    last_reminded_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["confirmed_at", "created_at"])]

    def __str__(self) -> str:
        return f"Sign-in attempt {self.pk}"

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None

    CONFIRMED = "confirmed"
    ALREADY_CONFIRMED = "already_confirmed"
    DROPPED = "dropped"

    def confirm(self) -> str:
        """Attach everything created during the attempt to the user.

        Locks the row, so it can't interleave with a concurrent drop
        (process_sign_in_attempts) or a second confirmation. Returns
        CONFIRMED, ALREADY_CONFIRMED (someone got there first: the
        link is single-use) or DROPPED.
        """
        from core.lifecycle import observations_for_attempt, promote

        with transaction.atomic():
            lock_user_first(self.user_id)
            locked = (
                SignInAttempt.objects.select_for_update()
                .filter(pk=self.pk)
                .first()
            )
            if locked is None:
                return self.DROPPED
            if locked.confirmed_at is not None:
                self.confirmed_at = locked.confirmed_at
                return self.ALREADY_CONFIRMED
            locked.confirmed_at = self.confirmed_at = timezone.now()
            locked.save(update_fields=["confirmed_at"])
            for queryset in observations_for_attempt(self):
                queryset.filter(user__isnull=True).update(user=self.user)
            promote(self.user)
        return self.CONFIRMED
