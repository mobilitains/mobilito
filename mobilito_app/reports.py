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

# Infrastructure reports (design §9.2, §21.4; roadmap Phase 6).
#
# /reports/new/  one page: map (Phase 4 widget), perspective, photos,
#                description, tags; submitted as one multipart form
# /reports/new/location/  htmx: confirm the spot, refresh the tags
#                          offered for its country (out of band)
# /reports/<id>/  the report
# /reports/<id>/photos/<media_id>/  a photo, only if it may be seen

import logging
import uuid

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import (
    require_GET,
    require_http_methods,
    require_POST,
)

from authentication.provisional import get_observer, observer_required
from core.forms import LocationConfirmForm
from core.geo import edge_point, make_point
from core.geocoding import GeocodeResult
from core.images import process_photo
from core.lifecycle import submission_state
from core.locations import new_location_fields
from core.maps import map_widget_config
from core.models import Location, LocationEvidence, PublicationState
from core.ratelimit import client_ip, is_rate_limited
from core.views import confirm_location_context
from mobilito_app.forms import ReportForm
from mobilito_app.models import (
    InfrastructureMedia,
    InfrastructureObservation,
    InfrastructureTag,
    TagStatus,
)

logger = logging.getLogger(__name__)

WIDGET_ID = "report-map"


def tags_for_country(country: str):
    """Active tags that apply in this country (§20.7).

    Blank country on a tag means universal. Without a country (no
    geocoding result), only universal tags are offered.
    """
    scope = Q(country="")
    if country:
        scope |= Q(country=country.upper())
    return InfrastructureTag.objects.filter(
        scope, status=TagStatus.ACTIVE
    ).order_by("family", "label")


def _map(request, confirmed=None):
    center = None
    if confirmed:
        data = confirmed["form"].cleaned_data
        center = (data["lat"], data["lon"])
    return map_widget_config(
        request,
        widget_id=WIDGET_ID,
        center=center,
        zoom=18 if center else None,
        confirmed=confirmed,
        crosshair=True,
        gps=True,
        confirm_url=reverse("reports_location"),
        # Tags already ticked survive a re-confirmation.
        confirm_extra_include="#report-tags [name=tags]:checked",
        confirm_extra_params=["tags"],
    )


# (value, label, icons)
PERSPECTIVES = [
    ("ped", gettext_lazy("On foot"), ["bi-person-walking"]),
    ("bike", gettext_lazy("By bike"), ["bi-bicycle"]),
    ("both", gettext_lazy("Both"), ["bi-person-walking", "bi-bicycle"]),
]


def _confirmed(form, location_fields):
    """Re-show the confirmed spot when the form comes back with errors."""
    if location_fields is None:
        return None
    data = form.cleaned_data
    if "lat" not in data or "lon" not in data:
        return None
    bound = LocationConfirmForm(
        {
            f"map-{name}": "" if data.get(name) is None else data[name]
            for name in ("lat", "lon", "device_lat", "device_lon")
            + ("device_accuracy",)
        },
        prefix="map",
    )
    bound.is_valid()
    return {
        "form": bound,
        "result": GeocodeResult(address=data.get("address", "")),
    }


def _render_form(
    request, form, status=200, country=None, location_fields=None
):
    return render(
        request,
        "mobilito_app/reports/new.html",
        {
            "form": form,
            "map": _map(request, _confirmed(form, location_fields)),
            "tags": tags_for_country(country) if country is not None else None,
            "checked_tags": (
                set(form.data.getlist("tags")) if form.is_bound else set()
            ),
            "perspectives": PERSPECTIVES,
            "max_photos": settings.PHOTO_MAX_PER_REPORT,
            "max_photo_mb": settings.PHOTO_MAX_UPLOAD_BYTES // (1024 * 1024),
            "max_photo_bytes": settings.PHOTO_MAX_UPLOAD_BYTES,
        },
        status=status,
    )


@observer_required
@require_GET
def new_report(request):
    """The report form (§21.4 "Confirms crosshair location → …")."""
    return _render_form(request, _blank_form())


def _blank_form():
    return ReportForm(initial={"submission_id": uuid.uuid4()})


def _submission_id(request):
    try:
        return uuid.UUID(request.POST.get("submission_id", ""))
    except ValueError:
        return None


def _show_already_sent(request, report):
    messages.info(
        request,
        _(
            "You had already sent this form, so nothing new was saved. "
            "Here is that report. To report something else, tap “Report "
            "something else”."
        ),
    )
    return redirect("reports_detail", pk=report.pk)


def _key_in_use(key):
    return (
        key is not None
        and InfrastructureObservation.objects.filter(
            client_submission_id=key
        ).exists()
    )


def _already_sent(request, key):
    """This observer's report made from the same form, if any."""
    if key is None:
        return None
    report = InfrastructureObservation.objects.filter(
        client_submission_id=key
    ).first()
    if report is not None and request.observer.owns(report):
        return report
    return None


@observer_required
@require_POST
def confirm_location(request):
    """Confirm the spot; also refresh the tags for its country."""
    context = confirm_location_context(request)
    result = context.get("result")
    context["tags"] = tags_for_country(result.country if result else "")
    context["checked_tags"] = {
        value for value in request.POST.getlist("tags") if value.isdigit()
    }
    return render(
        request, "mobilito_app/reports/location_confirmed.html", context
    )


@observer_required
@require_http_methods(["GET", "POST"])
def submit_report(request):
    """Create the report, its photos and location evidence (§9.2).

    Error pages are served at this URL: a GET here (history, a
    restored tab, a language switch) goes back to the form.
    """
    if request.method == "GET":
        return redirect("reports_new")
    # The same form sent again (Back then Send, a retry after the
    # connection dropped mid-upload): show the report it made.
    key = _submission_id(request)
    sent = _already_sent(request, key)
    if sent is not None:
        return _show_already_sent(request, sent)
    if is_rate_limited(
        "report_submit", client_ip(request), *settings.RATE_LIMIT_REPORT_SUBMIT
    ):
        # Not a blank page after a long upload: the form again, with
        # why. The browser's draft brings back the text and choices.
        messages.warning(
            request,
            _(
                "You've sent a lot of reports in a short time. Please "
                "wait a while and try again."
            ),
        )
        return _render_form(request, _blank_form(), status=429)
    form = ReportForm(request.POST)
    photos = request.FILES.getlist("photos")
    form.is_valid()
    if form.is_bot():
        logger.warning("Honeypot filled on report submit; ignoring")
        return redirect("home")

    location_fields = None
    data = form.cleaned_data
    # Whenever the location itself is valid, keep it, even if something
    # else needs fixing: losing a confirmed spot to a missed button is
    # the worst kind of error (the form comes back with it confirmed).
    if "lat" in data and "lon" in data:
        point = make_point(data["lat"], data["lon"])
        location_fields = new_location_fields(point, data.get("address", ""))
        # No country means this second geocode failed (the one at
        # confirm time found the tags shown): don't refuse them then.
        country = location_fields["country"]
        allowed = set(tags_for_country(country).values_list("pk", flat=True))
        tags = data.get("tags", [])
        if country and any(tag.pk not in allowed for tag in tags):
            form.add_error("tags", _("Choose tags from the list shown."))

    processed = []
    if not photos:
        form.add_error(None, _("Add at least one photo."))
    elif len(photos) > settings.PHOTO_MAX_PER_REPORT:
        form.add_error(
            None,
            _("At most %(n)s photos, please.")
            % {"n": settings.PHOTO_MAX_PER_REPORT},
        )
    else:
        for photo in photos:
            try:
                processed.append(process_photo(photo))
            except ValidationError as err:
                form.add_error(None, err)

    if form.errors:
        return _render_form(
            request,
            form,
            status=400,
            country=location_fields["country"] if location_fields else None,
            location_fields=location_fields,
        )

    if _key_in_use(key):
        key = None  # someone else's form: don't reuse its key
    try:
        report = _create_report(
            request,
            form.cleaned_data,
            location_fields,
            processed,
            request.observer.owner_fields(),
            key,
        )
    except IntegrityError:
        # The same form, sent twice at once: the other one won.
        sent = _already_sent(request, key)
        if sent is None:
            raise
        return _show_already_sent(request, sent)
    messages.success(request, _("Thank you! Your report has been sent."))
    return redirect("reports_detail", pk=report.pk)


def _create_report(request, data, location_fields, processed, owner, key):
    """Create the rows and store the photos: all or nothing.

    Each photo file is written to storage as its row is saved, which
    a rollback doesn't undo: if anything fails, delete them.
    """
    point = make_point(data["lat"], data["lon"])
    device = None
    if data["device_lat"] is not None and data["device_lon"] is not None:
        device = make_point(data["device_lat"], data["device_lon"])
    exif = next((p.exif_point for p in processed if p.exif_point), None)
    stored = []
    try:
        with transaction.atomic():
            location = Location.objects.create(**location_fields)
            report = InfrastructureObservation.objects.create(
                location=location,
                observer_perspective=data["perspective"],
                description=data["description"],
                publication_state=submission_state(owner.get("user")),
                client_submission_id=key,
                **owner,
            )
            report.tags.set(data["tags"])
            for photo in processed:
                item = InfrastructureMedia(
                    observation=report,
                    added_by=owner.get("user"),
                    exif_point=photo.exif_point,
                )
                item.file.save(photo.content.name, photo.content, save=False)
                stored.append(item.file.name)
                item.save()
            LocationEvidence.objects.create(
                observation=report,
                device_point=device,
                user_adjusted_point=point,
                edge_point=edge_point(request),
                exif_point=exif,
                accuracy_metres=data["device_accuracy"],
                timestamp=timezone.now(),
            )
    except BaseException:
        storage = InfrastructureMedia._meta.get_field("file").storage
        for name in stored:
            try:
                storage.delete(name)
            except Exception:  # noqa: BLE001 - keep the first error
                logger.exception("Couldn't delete orphan photo %s", name)
        raise
    return report


def _visible_report(request, pk):
    report = get_object_or_404(
        InfrastructureObservation.objects.select_related("location"), pk=pk
    )
    observer = get_observer(request)
    if observer.owns(report):
        return report, True
    if report.publication_state == PublicationState.PUBLISHED:
        return report, False
    raise Http404


@require_GET
def detail(request, pk):
    report, is_owner = _visible_report(request, pk)
    media = report.media.all()
    if not is_owner:
        media = media.filter(published=True)
    return render(
        request,
        "mobilito_app/reports/detail.html",
        {
            "report": report,
            "media": media,
            "tags": report.tags.all(),
            "is_owner": is_owner,
            "published": report.publication_state
            == PublicationState.PUBLISHED,
            "pending_validation": report.publication_state
            == PublicationState.PENDING_VALIDATION,
            "share_url": request.build_absolute_uri(),
        },
    )


@require_GET
def photo(request, pk, media_id):
    """Stream a photo, only if its report (and the photo) may be seen.

    Photos have no public URL of their own (private storage): this
    is the only way to them.
    """
    report, is_owner = _visible_report(request, pk)
    item = get_object_or_404(
        InfrastructureMedia, pk=media_id, observation=report
    )
    if not (is_owner or item.published):
        raise Http404
    try:
        handle = item.file.open("rb")
    except OSError:
        # Row without its file (lost, or a failed cleanup).
        logger.warning("Photo file missing: %s", item.file.name)
        raise Http404
    response = FileResponse(handle, content_type="image/jpeg")
    # Never in shared caches: a photo can be unpublished after
    # moderation, and must then stop being served.
    response["Cache-Control"] = "private, max-age=600"
    return response
