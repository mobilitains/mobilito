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

# Photo uploads (design §9.2, §20.6, §23.2; roadmap Phase 6).
#
# Every uploaded photo is opened with Pillow (anything that isn't an
# image is refused), turned upright, scaled down and re-encoded as a
# JPEG. Re-encoding drops all metadata: EXIF can carry the camera's
# serial number and, for photos taken at home, where someone lives.
# The GPS position is read first and kept apart, as location evidence
# (§11.1), never published with the image.

import io
import logging
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.utils.translation import gettext as _
from PIL import Image, ImageOps

from core.geo import make_point

logger = logging.getLogger(__name__)

GPS_IFD = 0x8825


@dataclass
class ProcessedPhoto:
    content: ContentFile  # JPEG, metadata stripped, random name
    exif_point: object  # a Point, or None


# Formats phones and cameras produce. Pillow reads many more, some
# through fragile plugins (and EPS through Ghostscript): refuse them.
# (Multi-picture JPEGs come through the JPEG opener as MPO.)
ALLOWED_FORMATS = ["JPEG", "PNG", "WEBP", "GIF"]


def _ref(value) -> str:
    """A GPS hemisphere reference ("N", "S", "E", "W"), normalised.

    Some cameras store it as bytes, NUL-padded or lower case.
    """
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    return str(value or "").strip("\x00 ").upper()[:1]


def _to_degrees(value) -> float:
    """EXIF GPS (degrees, minutes, seconds) rationals to a float."""
    degrees, minutes, seconds = (float(part) for part in value)
    # Seconds of exactly 60 do occur (rounding in camera firmware).
    if not (0 <= minutes < 60 and 0 <= seconds <= 60 and degrees >= 0):
        raise ValueError("GPS component out of range")
    return degrees + minutes / 60 + seconds / 3600


def exif_point(image):
    """The photo's GPS position from its EXIF data, or None."""
    try:
        gps = image.getexif().get_ifd(GPS_IFD)
        lat = _to_degrees(gps[2])
        lon = _to_degrees(gps[4])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    if _ref(gps.get(1)) == "S":
        lat = -lat
    if _ref(gps.get(3)) == "W":
        lon = -lon
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat, lon) == (0, 0):
        return None
    return make_point(lat, lon)


def _unreadable(upload):
    return ValidationError(
        _("“%(name)s” isn't a photo we can read (JPEG or PNG work best).")
        % {"name": upload.name}
    )


def process_photo(upload) -> ProcessedPhoto:
    """Validate and re-encode one uploaded photo.

    Raises ValidationError, with a message fit to show the user, for
    anything too large or that isn't a readable image.
    """
    if upload.size > settings.PHOTO_MAX_UPLOAD_BYTES:
        raise ValidationError(
            _("“%(name)s” is too large (at most %(mb)s MB per photo).")
            % {
                "name": upload.name,
                "mb": settings.PHOTO_MAX_UPLOAD_BYTES // (1024 * 1024),
            }
        )
    limit = settings.PHOTO_MAX_DIMENSION
    try:
        image = Image.open(upload, formats=ALLOWED_FORMATS)
        # Only the header is read so far: refuse huge images before
        # decoding a single pixel (a small file can decode to GBs).
        cap = settings.PHOTO_MAX_PIXELS.get(image.format, 0)
        if image.width * image.height > cap:
            raise _unreadable(upload)
        point = exif_point(image)
        # JPEG: decode straight at a reduced scale, cheaply.
        image.draft("RGB", (limit, limit))
        image.load()
        # Thumbnail first, then turn upright: the box is square, so
        # the result is the same, for a fraction of the memory.
        image.thumbnail((limit, limit))
        image = ImageOps.exif_transpose(image)
        if image.mode in ("RGBA", "LA", "PA") or (
            "transparency" in image.info
        ):
            # Transparent areas on white, not on whatever colour the
            # hidden pixels happen to hold.
            rgba = image.convert("RGBA")
            image = Image.new("RGB", rgba.size, "white")
            image.paste(rgba, mask=rgba.getchannel("A"))
        elif image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        # Nothing carried over: no EXIF, comment, XMP, ICC or other
        # metadata (§23.2).
        image.info.clear()
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=settings.PHOTO_JPEG_QUALITY)
    except ValidationError:
        raise
    except Exception as err:
        # Pillow's many format plugins raise many exception types for
        # corrupt input; to the user they all mean the same. Logged,
        # so that a genuine bug here doesn't pass unnoticed.
        logger.warning("Photo rejected as unreadable", exc_info=True)
        raise _unreadable(upload) from err
    return ProcessedPhoto(
        content=ContentFile(buffer.getvalue(), name=f"{uuid.uuid4().hex}.jpg"),
        exif_point=point,
    )
