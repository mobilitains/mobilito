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

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
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
