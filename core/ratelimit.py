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

import hashlib

from django.conf import settings
from django.core.cache import cache


def client_ip(request) -> str:
    """Return the client's IP address for rate-limiting purposes.

    Uses settings.RATE_LIMIT_CLIENT_IP_HEADER when set (e.g. behind
    Cloudflare), else REMOTE_ADDR.
    """
    header = getattr(settings, "RATE_LIMIT_CLIENT_IP_HEADER", None)
    if header:
        value = request.META.get(header, "")
        if value:
            # Proxies may append: the first entry is the client.
            return value.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def is_rate_limited(scope: str, ident: str, limit: int, window: int) -> bool:
    """Count one hit for (scope, ident); return True if over limit.

    Fixed-window counter in the default cache: at most `limit` hits
    per `window` seconds. The identifier is hashed so keys stay short
    and personal data (IPs, email addresses) isn't stored verbatim.
    """
    digest = hashlib.sha256(ident.encode("utf-8")).hexdigest()
    key = f"ratelimit:{scope}:{digest}"
    if cache.add(key, 1, timeout=window):
        count = 1
    else:
        try:
            count = cache.incr(key)
        except ValueError:
            # Expired between add() and incr().
            cache.set(key, 1, timeout=window)
            count = 1
    return count > limit
