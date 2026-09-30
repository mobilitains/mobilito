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

from django.shortcuts import render

from authentication.provisional import get_observer
from mobilito_app.counts import open_session


def home(request):
    """Landing page (§21.1) / authenticated home screen (§21.2).

    One URL, one template: the template itself branches on
    whether anyone is observing (signed in, or "probably signed in",
    §5.4) to swap the visitor CTAs for the two
    observation-entry buttons.
    """
    observer = get_observer(request)
    return render(
        request,
        "mobilito_app/home.html",
        {"observer": observer, "open_session": open_session(observer)},
    )
