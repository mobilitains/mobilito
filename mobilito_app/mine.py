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

# Your own observations (roadmap Phase 7): every count and report
# you made, including those others can't see yet, with where each
# stands in plain language (design §14: never the state names).
#
# /observations/mine/

from urllib.parse import urlencode


from django.core.paginator import Paginator
from django.db.models import CharField, F, Value
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET

from authentication.provisional import get_observer
from core.models import PublicationState as State
from mobilito_app.browse import (
    describe_count,
    published_counts,
    published_reports,
)
from mobilito_app.counts import is_stale
from mobilito_app.models import InfrastructureObservation, ModalShareSession

PAGE_SIZE = 20

# What each state means for the author: a short badge, a sentence
# saying what happens next, and a colour. Light hold is reachable by
# its link but off the map and list (§13.2). Sandboxed says nothing
# about why.
CHECKING = (
    _("Waiting to be checked"),
    _("Others will see it once it has been checked."),
    "secondary",
)
HIDDEN = (_("Not visible to others"), "", "secondary")
PLAIN_STATES = {
    State.DRAFT: (_("Not sent yet"), "", "secondary"),
    State.SUBMITTED: CHECKING,
    State.PENDING_VALIDATION: (
        _("Open the link we emailed you"),
        _("Once you have, it will be checked, then shown to others."),
        "warning",
    ),
    State.PENDING_MODERATION: CHECKING,
    State.PUBLISHED: (_("Visible to everyone"), "", "success"),
    State.LIGHT_HOLD: (
        _("Being checked"),
        _("People with the link can open it; it's off the map for now."),
        "warning",
    ),
    State.SANDBOXED: HIDDEN,
}
STILL_COUNTING = (
    _("Still counting"),
    _("Open it to carry on counting."),
    "primary",
)
NOT_FINISHED = (
    _("Not finished"),
    _("Open it to decide what to do with it."),
    "secondary",
)


def plain_state(observation) -> tuple:
    """(Badge, what happens next, Bootstrap colour) for the author."""
    if getattr(observation, "finished_at", True) is None:
        return NOT_FINISHED if is_stale(observation) else STILL_COUNTING
    return PLAIN_STATES.get(observation.publication_state, CHECKING)


def _rows(queryset, kind, when):
    return queryset.annotate(
        kind=Value(kind, output_field=CharField()), when=F(when)
    ).values("kind", "pk", "when")


@require_GET
def my_observations(request):
    """Everything this observer made, newest first."""
    observer = get_observer(request)
    if not observer.is_known:
        # Coming back for past observations means signing in, not
        # starting afresh with a new email.
        return redirect(
            reverse("auth_start")
            + "?"
            + urlencode({"next": request.get_full_path()})
        )
    counts = observer.own(ModalShareSession.objects)
    reports = observer.own(InfrastructureObservation.objects)
    rows = (
        _rows(counts, "count", "started_at")
        .union(_rows(reports, "report", "created_at"), all=True)
        .order_by("-when", "kind", "-pk")
    )
    page = Paginator(rows, PAGE_SIZE).get_page(request.GET.get("page"))
    wanted = {"count": [], "report": []}
    for row in page.object_list:
        wanted[row["kind"]].append(row["pk"])
    found = {
        ("count", c.pk): c
        for c in counts.select_related("location").filter(
            pk__in=wanted["count"]
        )
    }
    found.update(
        {
            ("report", r.pk): r
            for r in reports.select_related("location").filter(
                pk__in=wanted["report"]
            )
        }
    )
    items = []
    for row in page.object_list:
        item = found.get((row["kind"], row["pk"]))
        if item is None:
            continue  # discarded meanwhile
        if row["kind"] == "count" and item.finished_at is not None:
            describe_count(item)
        status, next_step, colour = plain_state(item)
        items.append(
            {
                "kind": row["kind"],
                "item": item,
                "status": status,
                "next_step": next_step,
                "colour": colour,
            }
        )
    return render(
        request,
        "mobilito_app/browse/mine.html",
        {
            "page": page,
            "items": items,
            # Only what this browser's sign-in covers (§5.4): say so,
            # or a volunteer on a new phone thinks their past is lost.
            "provisional": observer.user is None,
            "confirmed_elsewhere": observer.user is None
            and observer.attempt.is_confirmed,
            "sign_in_url": reverse("auth_start")
            + "?"
            + urlencode({"next": request.get_full_path()}),
            "visible": (
                counts.filter(pk__in=published_counts()).count()
                + reports.filter(pk__in=published_reports()).count()
            ),
        },
    )
