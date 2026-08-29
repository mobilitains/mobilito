#!/usr/bin/env python
# coding=utf8
"""Check that the PR title follows project conventions.

Rules:
  - Starts with a capital letter.
  - No longer than 52 characters (subject line target).
  - Does not end with a period.

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

import json
import os
import re
import sys

TITLE_RE = re.compile(r"^[A-Z].{0,51}[^.]$")


def check_pr_title():
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as fh:
        event = json.load(fh)
    title = event["pull_request"]["title"]
    print(f"PR title: {title!r}")
    if not TITLE_RE.match(title):
        print(
            "Error: PR title must start with a capital letter, "
            "be ≤52 characters, and not end with a period.",
            file=sys.stderr,
        )
        sys.exit(1)
    print("PR title OK.")


if __name__ == "__main__":
    check_pr_title()
