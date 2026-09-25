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

import os
from pathlib import Path

from django.contrib.messages import constants as message_constants

BASE_DIR = Path(__file__).resolve().parent.parent

# Override in settings_local.py or via DJANGO_SECRET_KEY env var.
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-placeholder-not-for-production",
)

DEBUG = True
ALLOWED_HOSTS = []

INSTALLED_APPS = [
    # modeltranslation must precede django.contrib.admin so it can
    # patch ModelAdmin classes for translated fields.
    "modeltranslation",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",
    # Third-party
    "django_htmx",
    "sesame",
    # Local
    "authentication",
    "core",
    "mobilito_app",
]

AUTH_USER_MODEL = "authentication.MobilitoUser"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "sesame.backends.ModelBackend",
]

# Magic-link tokens expire after 30 minutes.
SESAME_MAX_AGE = 1800
# Each magic link signs the user in once. The verify view only
# consumes the token on POST (after the user taps "Sign in"), so
# email link scanners that prefetch the GET don't burn it.
SESAME_ONE_TIME = True

LOGIN_URL = "auth_start"

# Bootstrap names its red alert "danger", not "error".
MESSAGE_TAGS = {message_constants.ERROR: "danger"}
LOGOUT_REDIRECT_URL = "home"

# Email. Production uses AWS SES through its SMTP interface (see
# doc/roadmap.md, decisions table), configured entirely from the
# environment. Without EMAIL_HOST, mail is printed to the console,
# which is what the Docker dev environment wants.
if os.environ.get("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.environ["EMAIL_HOST"]
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
    # SES: STARTTLS on 587 (default), or implicit TLS on 465.
    EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "") == "1"
    EMAIL_USE_TLS = not EMAIL_USE_SSL
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = os.environ.get(
    "DJANGO_DEFAULT_FROM_EMAIL", "Mobilito <noreply@example.com>"
)

# Server-side rate limits (§16), as (max requests, window seconds).
# Counters live in the default cache. The local-memory default is
# per-process, so production with several workers should configure
# a shared cache (e.g. memcached or Redis) in settings_local.py.
# Per-IP is generous: a volunteer evening on shared wifi, or mobile
# carrier NAT, puts many real people behind one address.
RATE_LIMIT_AUTH_START_PER_IP = (60, 3600)
RATE_LIMIT_AUTH_START_PER_EMAIL = (5, 3600)
# Request header holding the real client IP when behind a trusted
# proxy, in request.META form, e.g. "HTTP_CF_CONNECTING_IP" behind
# Cloudflare. None means use REMOTE_ADDR. Never set this unless the
# proxy is guaranteed to overwrite the header: clients can forge it.
RATE_LIMIT_CLIENT_IP_HEADER = None

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Must run after AuthenticationMiddleware (needs request.user) and
    # before LocaleMiddleware (whose detection should see the sync).
    "core.middleware.SyncUserLanguageMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "web_infra.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
            ],
        },
    },
]

WSGI_APPLICATION = "web_infra.wsgi.application"

# PostGIS database. Connection details come from environment variables so that
# Docker, CI, and local dev can all override without touching committed files.
DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": os.environ.get("POSTGRES_DB", "mobilito"),
        "USER": os.environ.get("POSTGRES_USER", "mobilito"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "mobilito"),
        "HOST": os.environ.get("POSTGRES_HOST", "db"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

_pw = "django.contrib.auth.password_validation."
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": f"{_pw}UserAttributeSimilarityValidator"},
    {"NAME": f"{_pw}MinimumLengthValidator"},
    {"NAME": f"{_pw}CommonPasswordValidator"},
    {"NAME": f"{_pw}NumericPasswordValidator"},
]

# Internationalisation
LANGUAGE_CODE = "fr-fr"
LANGUAGES = [
    ("fr", "Français"),
    ("en", "English"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

MODELTRANSLATION_LANGUAGES = ("fr", "en")
MODELTRANSLATION_DEFAULT_LANGUAGE = "fr"

# Location equivalence radii (§11.4): two observations are treated as
# "at the same location" if within this distance. Infrastructure uses
# a small radius so opposite sides of a street aren't conflated;
# modal share uses a larger approximation standing in for "the same
# unbranched stretch of road", since precise path topology is not
# modelled.
LOCATION_EQUIVALENCE_RADIUS_INFRASTRUCTURE_METERS = 3
LOCATION_EQUIVALENCE_RADIUS_MODAL_SHARE_METERS = 50

# Reverse geocoding (roadmap Phase 4, core/geocoding.py). Swap the
# provider here, e.g. to "core.geocoding.MapboxGeocoder" (which also
# needs MAPBOX_ACCESS_TOKEN), without touching views.
GEOCODING_BACKEND = os.environ.get(
    "GEOCODING_BACKEND", "core.geocoding.NominatimGeocoder"
)
NOMINATIM_URL = os.environ.get(
    "NOMINATIM_URL", "https://nominatim.openstreetmap.org/reverse"
)
MAPBOX_ACCESS_TOKEN = os.environ.get("MAPBOX_ACCESS_TOKEN", "")
# Paid Mapbox feature: required before results may be stored.
MAPBOX_PERMANENT = os.environ.get("MAPBOX_PERMANENT", "") == "1"
# Nominatim's usage policy requires a User-Agent identifying the app
# with a reachable contact; set a contact email in production.
GEOCODING_USER_AGENT = os.environ.get(
    "GEOCODING_USER_AGENT",
    "Mobilito/0.1 (+https://github.com/mobilitains/mobilito)",
)
GEOCODING_TIMEOUT_SECONDS = 5
# How long a user waits for Nominatim's 1 request/second slot before
# we give up and let them type the address instead.
GEOCODING_MAX_WAIT_SECONDS = 2
GEOCODING_CACHE_SECONDS = 30 * 24 * 3600
RATE_LIMIT_LOCATION_CONFIRM = (30, 60)

# Map defaults (roadmap Phase 4): where the map opens when there is
# no better information. Nantes, where Mobilito starts.
MAP_DEFAULT_CENTER = (47.2184, -1.5536)  # (lat, lon)
MAP_DEFAULT_ZOOM = 13
MAP_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
MAP_TILE_ATTRIBUTION = (
    '&copy; <a href="https://www.openstreetmap.org/copyright">'
    "OpenStreetMap</a> contributors"
)

# Static and media files
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}

# Load environment-specific overrides (never committed).
try:
    from .settings_local import *  # noqa: F401, F403
except ImportError:
    pass
