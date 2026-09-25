"""
Copyright 2024  Francais pour une Meilleure Mobilité

Author(s): Jeff Abrahamson <jeff@p27.eu>, based in part on the Mobilito
proof of concept in transport-nantes/tn_web.

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

# Modal share counting (design §9.1, §21.3; roadmap Phase 5).
#
# /counts/new/  pick the spot (map widget) and start
# /counts/<id>/count/  the four-button tap screen (count.js)
# /counts/<id>/event|finish|discard/  JSON endpoints for count.js
# /counts/<id>/  results

import json
import math
import logging
import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.conf import settings
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.http import Http404, HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_GET, require_POST

from authentication.provisional import get_observer, observer_required
from core.geo import distance_meters, edge_point, make_point
from core.geocoding import reverse_geocode
from core.lifecycle import submission_state
from core.maps import map_widget_config, uses_device_location
from core.models import Location, LocationEvidence, PublicationState
from core.ratelimit import client_ip, is_rate_limited
from mobilito_app.forms import CountStartForm
from mobilito_app.models import Mode, ModalShareCountEvent, ModalShareSession

logger = logging.getLogger(__name__)

# Plural labels, as on the counting buttons and the results.
MODE_LABELS = {
    "ped": gettext_lazy("Pedestrians"),
    # "Bikes", not "Cyclists": scooters and skateboards count here too
    # (design §9.1, things on the road by behaviour).
    "bike": gettext_lazy("Bikes"),
    "car": gettext_lazy("Cars"),
    "tc": gettext_lazy("Public transit"),
}

# How far a phone's clock may disagree with ours before a tap's own
# timestamp is replaced by the time we received it.
CLOCK_TOLERANCE = timedelta(minutes=5)


# Totals above this are not a real count.
MAX_TOTAL = 1_000_000


def open_session(observer):
    """The observer's own count still in progress, if recent (§18).

    So that leaving the counting page (back button, closed tab)
    doesn't strand a count: the start page and home offer to go
    back to it.
    """
    if not observer.is_known:
        return None
    since = timezone.now() - timedelta(hours=settings.MODAL_SHARE_RESUME_HOURS)
    return (
        observer.own(ModalShareSession.objects)
        .filter(finished_at__isnull=True, started_at__gte=since)
        .order_by("-started_at")
        .first()
    )


def _visible_session(request, pk):
    """A session this request may see: its own, or a published one.

    TODO(Phase 8): "light hold" observations are reachable by direct
    URL too (§14).
    """
    session = get_object_or_404(
        ModalShareSession.objects.select_related("location"), pk=pk
    )
    if get_observer(request).owns(session):
        return session
    if session.publication_state == PublicationState.PUBLISHED:
        return session
    raise Http404


def _own_session(request, pk):
    session = get_object_or_404(ModalShareSession, pk=pk)
    if not request.observer.owns(session):
        raise Http404
    return session


def _map_for(request, origin=None):
    center = None
    if origin is not None:
        point = origin.location.point
        center = (point.y, point.x)
    return map_widget_config(
        request,
        widget_id="count-map",
        center=center,
        zoom=18 if origin is not None else None,
        crosshair=True,
        gps=True,
    )


def _origin(request, value):
    """The session a "Do another count here" started from, if visible."""
    if not value or not str(value).isdigit():
        return None
    try:
        return _visible_session(request, int(value))
    except Http404:
        return None


@observer_required
@require_GET
def new_count(request):
    """Choose where to count (§21.3 steps 1–2)."""
    origin = _origin(request, request.GET.get("from"))
    form = CountStartForm(initial={"origin": origin.pk if origin else ""})
    return render(
        request,
        "mobilito_app/counts/new.html",
        {
            "form": form,
            "map": _map_for(request, origin),
            "origin": origin,
            "min_minutes": _min_minutes(),
            "open_session": open_session(request.observer),
        },
    )


def _min_minutes() -> int:
    return math.ceil(settings.MODAL_SHARE_MIN_SESSION_SECONDS / 60)


@observer_required
@require_POST
def start_count(request):
    form = CountStartForm(request.POST)
    origin = _origin(request, request.POST.get("origin"))
    if is_rate_limited(
        "count_start", client_ip(request), *settings.RATE_LIMIT_COUNT_START
    ):
        return HttpResponse(status=429)
    if not form.is_valid():
        return render(
            request,
            "mobilito_app/counts/new.html",
            {
                "form": form,
                "map": _map_for(request, origin),
                "origin": origin,
                "min_minutes": _min_minutes(),
                "open_session": open_session(request.observer),
            },
            status=400,
        )
    if form.is_bot():
        logger.warning("Honeypot filled on count start; ignoring")
        return redirect("home")

    data = form.cleaned_data
    now = timezone.now()
    point = make_point(data["lat"], data["lon"])
    device = None
    if data["device_lat"] is not None and data["device_lon"] is not None:
        device = make_point(data["device_lat"], data["device_lon"])
    # Outside the transaction: it may call a geocoding service.
    location_fields = _new_location_fields(point, data["address"], origin)
    with transaction.atomic():
        location = (
            origin.location
            if location_fields is None
            else Location.objects.create(**location_fields)
        )
        session = ModalShareSession.objects.create(
            location=location,
            started_at=now,
            publication_state=PublicationState.DRAFT,
            location_mismatch=_mismatch(
                device, point, data["device_accuracy"]
            ),
            **request.observer.owner_fields(),
        )
        LocationEvidence.objects.create(
            observation=session,
            device_point=device,
            user_adjusted_point=point,
            edge_point=edge_point(request),
            accuracy_metres=data["device_accuracy"],
            timestamp=now,
        )
    return redirect("counts_count", pk=session.pk)


def _mismatch(device, point, accuracy) -> bool:
    """Is the phone clearly somewhere else (§9.1 "Presence")?

    Allows for the fix's own uncertainty, so a vague fix near the
    spot isn't flagged.
    """
    if device is None:
        return False
    distance = distance_meters(device, point) - (accuracy or 0)
    return distance > settings.MODAL_SHARE_LOCATION_MISMATCH_METERS


def _new_location_fields(point, address, origin):
    """Fields for a new Location, or None to reuse the origin's.

    "Do another count here" (§9.1) links repeat counts through one
    Location, which is what the time-series view aggregates. A new
    spot, or one moved beyond the modal share equivalence radius,
    gets its own Location.
    """
    if origin is not None and (
        distance_meters(origin.location.point, point)
        <= settings.LOCATION_EQUIVALENCE_RADIUS_MODAL_SHARE_METERS
    ):
        return None
    suggestion = reverse_geocode(point.y, point.x, get_language())
    suggested = suggestion.address if suggestion else ""
    return {
        "point": point,
        "user_entered_address": address if address != suggested else "",
        "reverse_geocoded_address": suggested,
        "country": suggestion.country if suggestion else "",
        "region": suggestion.region if suggestion else "",
        "department": suggestion.department if suggestion else "",
        "commune": suggestion.commune if suggestion else "",
    }


@observer_required
@require_GET
def count(request, pk):
    """The four-button counting screen (§9.1 UI)."""
    session = _own_session(request, pk)
    if not session.is_open:
        return redirect("counts_detail", pk=session.pk)
    config = {
        "sessionId": session.pk,
        "startedAt": int(session.started_at.timestamp() * 1000),
        "minSeconds": settings.MODAL_SHARE_MIN_SESSION_SECONDS,
        "eventUrl": reverse("counts_event", args=[session.pk]),
        "finishUrl": reverse("counts_finish", args=[session.pk]),
        "discardUrl": reverse("counts_discard", args=[session.pk]),
        "totals": session.event_totals(),
        "csrfToken": get_token(request),
        # Lets the phone correct for its own clock.
        "serverNow": int(timezone.now().timestamp() * 1000),
        "useDeviceLocation": uses_device_location(request),
        "homeUrl": reverse("home"),
        "resultsUrl": reverse("counts_detail", args=[session.pk]),
    }
    return render(
        request,
        "mobilito_app/counts/count.html",
        {
            "session": session,
            "config": config,
            "modes": [
                (mode, MODE_LABELS[mode], icon)
                for mode, icon in (
                    ("ped", "bi-person-walking"),
                    ("bike", "bi-bicycle"),
                    ("car", "bi-car-front-fill"),
                    ("tc", "bi-bus-front-fill"),
                )
            ],
            "hide_unvalidated_banner": True,
            "min_minutes": _min_minutes(),
        },
    )


def _json_body(request):
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _client_time(value, session, now):
    """A tap's own timestamp (ms since epoch), if it's plausible."""
    try:
        when = datetime.fromtimestamp(float(value) / 1000, tz=dt_timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return now
    if session.started_at - CLOCK_TOLERANCE <= when <= now + CLOCK_TOLERANCE:
        return when
    return now


@observer_required
@require_POST
def record_event(request, pk):
    """Store one tap (§20.4). Idempotent per client event id."""
    session = _own_session(request, pk)
    if is_rate_limited(
        "count_event_session",
        str(session.pk),
        *settings.RATE_LIMIT_COUNT_EVENTS_PER_SESSION,
    ) or is_rate_limited(
        "count_event_ip",
        client_ip(request),
        *settings.RATE_LIMIT_COUNT_EVENTS_PER_IP,
    ):
        return JsonResponse({"error": "rate_limited"}, status=429)
    data = _json_body(request)
    if data is None or data.get("mode") not in Mode.values:
        return JsonResponse({"error": "invalid"}, status=400)
    try:
        event_id = uuid.UUID(str(data.get("event_id")))
    except ValueError:
        return JsonResponse({"error": "invalid"}, status=400)
    point = None
    try:
        lat, lon = float(data["lat"]), float(data["lon"])
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            point = make_point(lat, lon)
    except (KeyError, TypeError, ValueError):
        pass
    try:
        with transaction.atomic():
            # Shares the row lock finish takes: no tap can slip in
            # after the totals and integrity hash are recorded.
            session = (
                ModalShareSession.objects.select_for_update(no_key=True)
                .filter(pk=session.pk, finished_at__isnull=True)
                .first()
            )
            if session is None:
                return JsonResponse({"error": "finished"}, status=409)
            ModalShareCountEvent.objects.create(
                session=session,
                mode=data["mode"],
                timestamp=_client_time(
                    data.get("client_timestamp"), session, timezone.now()
                ),
                point=point,
                client_event_id=event_id,
            )
    except IntegrityError:
        pass  # Already stored: a retry after a lost response.
    return HttpResponse(status=204)


@observer_required
@require_POST
def finish(request, pk):
    """Close the session with the phone's own totals (§20.3)."""
    with transaction.atomic():
        session = _own_session(request, pk)
        session = ModalShareSession.objects.select_for_update().get(
            pk=session.pk
        )
        done = {"redirect": reverse("counts_detail", args=[session.pk])}
        if not session.is_open:
            return JsonResponse(done)  # a retried finish
        data = _json_body(request)
        totals = (data or {}).get("totals")
        if not isinstance(totals, dict):
            return JsonResponse({"error": "invalid"}, status=400)
        for mode, field in ModalShareSession.TOTAL_FIELDS.items():
            value = totals.get(str(mode), 0)
            # type(), not isinstance(): True/False are ints too.
            if type(value) is not int or not 0 <= value <= MAX_TOTAL:
                return JsonResponse({"error": "invalid"}, status=400)
            setattr(session, field, value)
        now = timezone.now()
        last_tap = (
            session.events.order_by("-timestamp")
            .values_list("timestamp", flat=True)
            .first()
        )
        session.finished_at = max(
            filter(
                None,
                [
                    _client_time(data.get("finished_at"), session, now),
                    session.started_at,
                    last_tap,
                ],
            )
        )
        session.publication_state = submission_state(session.user)
        session.integrity_hash = session.compute_integrity_hash()
        session.save()
    if session.totals() != session.event_totals():
        # Taps lost in transit, most likely; kept for moderators.
        logger.info("Count %s: reported totals differ from taps", session.pk)
    return JsonResponse(done)


@observer_required
@require_POST
def discard(request, pk):
    """Throw away an unfinished count (the "short session" choice)."""
    session = _own_session(request, pk)
    with transaction.atomic():
        session = (
            ModalShareSession.objects.select_for_update()
            .filter(pk=session.pk, finished_at__isnull=True)
            .select_related("location")
            .first()
        )
        if session is None:
            return JsonResponse({"error": "finished"}, status=409)
        location = session.location
        session.delete()
        try:
            # Keep the location if something still uses it. (A count
            # starting from this very session at this instant could
            # still race it; vanishingly unlikely, and it would fail
            # safe, with an error rather than lost data.)
            with transaction.atomic():
                if not (
                    location.modalsharesessions.exists()
                    or location.infrastructureobservations.exists()
                ):
                    location.delete()
        except ProtectedError:
            pass
    messages.info(request, _("Count discarded."))
    return JsonResponse({"redirect": reverse("home")})


@require_GET
def detail(request, pk):
    """Results of a count (§9.1 "On finish")."""
    session = _visible_session(request, pk)
    observer = get_observer(request)
    is_owner = observer.owns(session)
    if is_owner and session.is_open:
        return redirect("counts_count", pk=session.pk)
    totals = session.totals()
    total = sum(totals.values())
    bars = [
        {
            "mode": mode,
            "label": MODE_LABELS[mode],
            "count": totals[mode],
            "percent": round(100 * totals[mode] / total) if total else 0,
        }
        for mode in ("ped", "bike", "car", "tc")
    ]
    return render(
        request,
        "mobilito_app/counts/detail.html",
        {
            "session": session,
            "bars": bars,
            "total": total,
            "is_owner": is_owner,
            "published": session.publication_state
            == PublicationState.PUBLISHED,
            "duration_minutes": round(
                (session.finished_at - session.started_at).total_seconds() / 60
            ),
            "share_url": request.build_absolute_uri(),
        },
    )
