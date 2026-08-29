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
        self.email_validated = True
        self.save()
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
