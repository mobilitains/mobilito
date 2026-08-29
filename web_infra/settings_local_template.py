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

# Copy this file to settings_local.py and fill in values.
# settings_local.py is gitignored and never committed.
# It is loaded at the end of settings.py and overrides anything there.
#
# In the Docker dev environment (docker/docker-manage.sh), database settings
# are passed via environment variables and no settings_local.py is needed.
# Use this file for local dev without Docker.

SECRET_KEY = "change-me-to-a-real-secret-key"

# Uncomment and adjust for local dev without Docker.
# DATABASES = {
#     "default": {
#         "ENGINE": "django.contrib.gis.db.backends.postgis",
#         "NAME": "mobilito",
#         "USER": "mobilito",
#         "PASSWORD": "mobilito",
#         "HOST": "localhost",
#         "PORT": "5432",
#     }
# }

# If GeoDjango can't find the GDAL or GEOS libraries, set paths explicitly:
# GDAL_LIBRARY_PATH = "/usr/lib/libgdal.so"
# GEOS_LIBRARY_PATH = "/usr/lib/libgeos_c.so"

# DEBUG = False
# ALLOWED_HOSTS = ["mobilito.example.com"]
