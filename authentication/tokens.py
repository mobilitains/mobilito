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

# Confirmation links for provisional sign-ins (design §5.4).
#
# Not sesame tokens: sesame's single-use tokens are revoked whenever
# the user's last_login changes, so any sign-in (including confirming
# another attempt) would silently kill every other outstanding
# attempt link, and the data behind it would then be dropped with no
# way to save it. An attempt link is single-use through the
# attempt's own confirmed_at instead (set under a row lock).

from django.conf import settings
from django.core import signing

SALT = "mobilito.authentication.attempt"


def make_attempt_token(attempt) -> str:
    return signing.dumps(
        {"attempt": attempt.pk, "user": attempt.user_id}, salt=SALT
    )


def attempt_from_token(token: str):
    """Return the unconfirmed-or-not attempt a token names, or None.

    None for a bad signature, an expired token, an attempt that no
    longer exists (dropped), or one whose user can't sign in.
    """
    from authentication.models import SignInAttempt

    try:
        data = signing.loads(
            token, salt=SALT, max_age=settings.SIGN_IN_ATTEMPT_LINK_MAX_AGE
        )
    except signing.BadSignature:
        return None
    if not isinstance(data, dict):
        return None
    attempt = (
        SignInAttempt.objects.select_related("user")
        .filter(pk=data.get("attempt"), user_id=data.get("user"))
        .first()
    )
    if attempt is None or not attempt.user.is_active:
        return None
    return attempt
