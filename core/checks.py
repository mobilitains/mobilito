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

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

LOCAL_CACHES = (
    "django.core.cache.backends.locmem.LocMemCache",
    "django.core.cache.backends.dummy.DummyCache",
)


@register(Tags.caches, deploy=True)
def shared_cache_check(app_configs, **kwargs):
    """Rate limits and the Nominatim throttle need a shared cache.

    With a per-process cache, each worker keeps its own counters, so
    limits multiply by the number of workers and Nominatim's
    absolute 1 request/second policy can be broken.
    """
    backend = settings.CACHES.get("default", {}).get("BACKEND", "")
    if backend in LOCAL_CACHES:
        return [
            Warning(
                "The default cache is per-process, so rate limits and "
                "the Nominatim throttle aren't shared between workers.",
                hint="Configure a shared CACHES backend (e.g. Redis, "
                "memcached or the database cache) in settings_local.py.",
                id="core.W001",
            )
        ]
    return []


@register()
def sign_in_attempt_link_check(app_configs, **kwargs):
    """Every reminder's link must outlive the attempt it confirms.

    The last reminder goes out SIGN_IN_ATTEMPT_DROP_AFTER_DAYS before
    the drop; a link that expires sooner would leave people with no
    way to save their data (design §5.4).
    """
    if (
        settings.SIGN_IN_ATTEMPT_LINK_MAX_AGE
        < settings.SIGN_IN_ATTEMPT_DROP_AFTER_DAYS * 86400
    ):
        return [
            Error(
                "SIGN_IN_ATTEMPT_LINK_MAX_AGE is shorter than "
                "SIGN_IN_ATTEMPT_DROP_AFTER_DAYS.",
                hint="Links sent with the last reminder would expire "
                "before the data is dropped.",
                id="core.E001",
            )
        ]
    return []
