#!/usr/bin/env python
# coding=utf8
"""Check that the PR title follows project conventions.

Rules:
  - Starts with a capital letter.
  - No longer than 52 characters (subject line target).
  - Does not end with a period.
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
