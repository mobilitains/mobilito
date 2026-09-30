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
from django.contrib.gis.measure import D
from django.db import IntegrityError, transaction
from django.db.models import Count, ProtectedError
from django.http import Http404, HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_GET, require_POST

from authentication.provisional import observer_required
from core.geo import distance_meters, edge_point, make_point
from core.locations import new_location_fields
from core.lifecycle import submission_state
from core.maps import map_widget_config, uses_device_location
from core.models import Location, LocationEvidence, PublicationState
from core.ratelimit import client_ip, is_rate_limited
from mobilito_app.browse import published_counts
from mobilito_app.forms import CountStartForm
from mobilito_app.models import Mode, ModalShareCountEvent, ModalShareSession
from mobilito_app.moderation import OWNER, state_context, visible_or_404

logger = logging.getLogger(__name__)

# Plural labels, as on the counting buttons and the results.
# Always in this order: the bars and tables line up with the legend.
MODES = ("ped", "bike", "car", "tc")
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


def is_stale(session) -> bool:
    """Open, and too old to carry on counting (§18 resume window).

    A count left open for days mustn't take new taps or be finished
    "now": it would claim to have lasted days. It can only be kept as
    it stood at its last tap, or thrown away.
    """
    return session.finished_at is None and timezone.now() > count_deadline(
        session
    )


def count_deadline(session):
    """Latest moment a count can still take taps or end.

    Judged by when a tap was made, not when it arrives: taps queued
    offline (count.js) and sent later still count.
    """
    return session.started_at + timedelta(
        hours=settings.MODAL_SHARE_RESUME_HOURS
    )


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
    """(session, viewer role) if this request may see it (§13.2)."""
    return visible_or_404(request, ModalShareSession.objects, pk)


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
        return _visible_session(request, int(value))[0]
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
    Location. A new spot, or one moved beyond the modal share
    equivalence radius, gets its own Location. (The time-series view,
    history(), goes by that radius rather than the Location, so
    independent counts on the same stretch are compared too.)
    """
    if origin is not None and (
        distance_meters(origin.location.point, point)
        <= settings.LOCATION_EQUIVALENCE_RADIUS_MODAL_SHARE_METERS
    ):
        return None
    return new_location_fields(point, address)


@observer_required
@require_GET
def count(request, pk):
    """The four-button counting screen (§9.1 UI)."""
    session = _own_session(request, pk)
    if not session.is_open:
        return redirect("counts_detail", pk=session.pk)
    if is_stale(session):
        return render(
            request,
            "mobilito_app/counts/stale.html",
            {
                "session": session,
                "taps": session.events.count(),
                # For count_rescue.js: taps still queued on the phone.
                "rescue": {
                    "sessionId": session.pk,
                    "eventUrl": reverse("counts_event", args=[session.pk]),
                    "finishUrl": reverse("counts_finish", args=[session.pk]),
                    "csrfToken": get_token(request),
                },
            },
        )
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
            when = _client_time(
                data.get("client_timestamp"), session, timezone.now()
            )
            if when > count_deadline(session):
                # Tapped into a count left open for too long.
                return JsonResponse({"error": "finished"}, status=409)
            ModalShareCountEvent.objects.create(
                session=session,
                mode=data["mode"],
                timestamp=when,
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
        if session.finished_at > count_deadline(session):
            # Finished from a page left open for too long (a finish
            # queued offline within the window keeps its own time):
            # it ended at its last tap in time, with those taps as
            # its totals (the phone's may include later ones). As for
            # close_stale, nothing counted in time means nothing to
            # keep: refused for good (the phone then forgets it).
            if not _taps_in_time(session).exists():
                return JsonResponse({"error": "invalid"}, status=400)
            _apply_tap_totals(session)
            session.finished_at = _end_of_stale(session)
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
    if not _delete_open(session):
        return JsonResponse({"error": "finished"}, status=409)
    messages.info(request, _("Count discarded."))
    return JsonResponse({"redirect": reverse("home")})


def _taps_in_time(session):
    """Taps within the count's window (taps stored before the
    deadline was enforced could be later)."""
    return session.events.filter(timestamp__lte=count_deadline(session))


def _apply_tap_totals(session):
    """Totals from the taps in time, for a count closed after its
    window (the phone's own totals never arrived, or ran on)."""
    totals = {str(mode): 0 for mode in ModalShareSession.TOTAL_FIELDS}
    for row in _taps_in_time(session).values("mode").annotate(n=Count("pk")):
        totals[row["mode"]] = row["n"]
    for mode, field in ModalShareSession.TOTAL_FIELDS.items():
        setattr(session, field, totals[str(mode)])


def _end_of_stale(session):
    """When a count left open too long ended: its last tap in time,
    never before its start."""
    last_tap = (
        _taps_in_time(session)
        .order_by("-timestamp")
        .values_list("timestamp", flat=True)
        .first()
    )
    return max(filter(None, [last_tap, session.started_at]))


def _delete_open(session) -> bool:
    """Delete an unfinished count; False if it was finished meanwhile."""
    with transaction.atomic():
        session = (
            ModalShareSession.objects.select_for_update()
            .filter(pk=session.pk, finished_at__isnull=True)
            .select_related("location")
            .first()
        )
        if session is None:
            return False
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
    return True


@observer_required
@require_POST
def close_stale(request, pk):
    """Keep or throw away a count left open too long (a plain form).

    Kept, it ends at its last tap, with the totals of the taps the
    server received (the phone's own totals never arrived).
    """
    session = _own_session(request, pk)
    if not session.is_open:
        return redirect("counts_detail", pk=session.pk)
    if not is_stale(session):
        return redirect("counts_count", pk=session.pk)
    if request.POST.get("action") == "discard":
        if not _delete_open(session):
            # Finished meanwhile (an old tab): it was kept after all.
            return redirect("counts_detail", pk=session.pk)
        messages.info(request, _("Count thrown away."))
        return redirect("my_observations")
    with transaction.atomic():
        session = (
            ModalShareSession.objects.select_for_update()
            .filter(pk=session.pk, finished_at__isnull=True)
            .first()
        )
        if session is None:
            return redirect("counts_detail", pk=pk)
        if not _taps_in_time(session).exists():
            # Nothing was counted: nothing to keep.
            return redirect("counts_count", pk=session.pk)
        _apply_tap_totals(session)
        session.finished_at = _end_of_stale(session)
        session.publication_state = submission_state(session.user)
        session.integrity_hash = session.compute_integrity_hash()
        session.save()
    # Moderators can't otherwise tell it from a normal finish.
    logger.info("Count %s: kept after being left open", session.pk)
    messages.success(request, _("Count saved."))
    return redirect("counts_detail", pk=session.pk)


def totals_bars(session) -> dict:
    """Context for partials/count_bars.html: {"bars", "total"}."""
    totals = session.totals()
    total = sum(totals.values())
    bars = [
        {
            "mode": mode,
            "label": MODE_LABELS[mode],
            "count": totals[mode],
            "percent": round(100 * totals[mode] / total) if total else 0,
        }
        for mode in MODES
    ]
    return {"bars": bars, "total": total}


# Most counts shown in a spot's history (the latest ones).
HISTORY_MAX = 12


def history(session) -> list:
    """Counts at this spot over time (§9.1 "Time evolution").

    Published, finished counts within the modal share equivalence
    radius (§11.4: counts a few metres apart on one stretch of road
    see the same traffic), plus this one whatever its state or age
    (only its owner, or anyone once published, gets here). Oldest
    first: this one and the latest others, HISTORY_MAX in all. Each
    as shares of its total, since counts last different times.
    """
    radius = D(m=settings.LOCATION_EQUIVALENCE_RADIUS_MODAL_SHARE_METERS)
    others = (
        published_counts()
        .filter(location__point__dwithin=(session.location.point, radius))
        .exclude(pk=session.pk)
        # Nothing counted: no shares to compare.
        .exclude(total_pedestrian=0, total_cyclist=0, total_car=0, total_tc=0)
        .order_by("-started_at", "-pk")[: HISTORY_MAX - 1]
    )
    # This count always, however old.
    nearby = sorted(
        [*others, session], key=lambda count: (count.started_at, count.pk)
    )
    rows = []
    for count in nearby:
        totals = count.totals()
        total = sum(totals.values())
        rows.append(
            {
                "count": count,
                "is_this": count.pk == session.pk,
                # Shown in local time: compare years the same way.
                "year": timezone.localtime(count.started_at).year,
                "total": total,
                "minutes": round(
                    (count.finished_at - count.started_at).total_seconds() / 60
                ),
                "modes": [
                    {
                        "mode": mode,
                        "label": MODE_LABELS[mode],
                        "count": totals[mode],
                        "percent": (
                            round(100 * totals[mode] / total) if total else 0
                        ),
                    }
                    for mode in MODES
                ],
            }
        )
    return rows


@require_GET
def detail(request, pk):
    """Results of a count (§9.1 "On finish")."""
    session, role = _visible_session(request, pk)
    if session.is_open:
        # Still counting: nothing to show anyone but its counter.
        if role == OWNER:
            return redirect("counts_count", pk=session.pk)
        raise Http404
    return render(
        request,
        "mobilito_app/counts/detail.html",
        {
            "session": session,
            **totals_bars(session),
            "history": history(session),
            "now_year": timezone.localdate().year,
            "mode_labels": [(mode, MODE_LABELS[mode]) for mode in MODES],
            **state_context(session, role),
            "duration_minutes": round(
                (session.finished_at - session.started_at).total_seconds() / 60
            ),
            "share_url": request.build_absolute_uri(),
        },
    )


@require_GET
def summary(request, pk):
    """A count in the map's bottom sheet (§9.2 "Selecting ...")."""
    session, _role = _visible_session(request, pk)
    if session.is_open:
        raise Http404
    return render(
        request,
        "mobilito_app/counts/summary.html",
        {"session": session, **totals_bars(session)},
    )
