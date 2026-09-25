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

import logging
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone, translation

from authentication.email import send_magic_link
from authentication.models import SignInAttempt
from authentication.provisional import drop_attempt
from core.lifecycle import observations_for_attempt

logger = logging.getLogger(__name__)


def reminder_delays():
    return sorted(
        timedelta(hours=hours)
        for hours in settings.SIGN_IN_ATTEMPT_REMINDER_DELAYS_HOURS
    )


def drop_after():
    return timedelta(days=settings.SIGN_IN_ATTEMPT_DROP_AFTER_DAYS)


def has_data(attempt) -> bool:
    return any(qs.exists() for qs in observations_for_attempt(attempt))


def drop_time(attempt, recorded: bool):
    """When an unconfirmed attempt goes, fixed from its start.

    Anchored on created_at (not on when reminders actually went out)
    so that the date a reminder announces holds even if the command
    was down for a while or an email failed.
    """
    delays = reminder_delays()
    if recorded and delays:
        return attempt.created_at + delays[-1] + drop_after()
    return attempt.created_at + drop_after()


class Command(BaseCommand):
    help = (
        "Remind, then drop, provisional sign-ins that were never "
        "confirmed (design §5.4). Run regularly, e.g. hourly from cron."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would happen without sending or deleting.",
        )

    def handle(self, *args, dry_run=False, **options):
        now = timezone.now()
        counts = {"reminded": 0, "dropped": 0, "failed": 0}
        pending = SignInAttempt.objects.filter(
            confirmed_at__isnull=True
        ).select_related("user")
        for attempt in pending:
            try:
                action = self.process(attempt, now, dry_run)
            except Exception:
                # One bad row mustn't block every other attempt.
                logger.exception("Failed to process sign-in attempt")
                action = "failed"
            if action:
                counts[action] += 1
        prefix = "Would have: " if dry_run else ""
        self.stdout.write(
            f"{prefix}reminded {counts['reminded']}, dropped "
            f"{counts['dropped']}, failed {counts['failed']} unconfirmed "
            "sign-in attempt(s)."
        )

    def process(self, attempt, now, dry_run):
        recorded = has_data(attempt)
        drop_at = drop_time(attempt, recorded)
        if now >= drop_at:
            if dry_run or drop_attempt(attempt):
                return "dropped"
            return None
        if not recorded or not attempt.user.is_active:
            # Nothing worth a reminder, or a link that can't work.
            return None
        # Reminders due by now; after downtime, one email covers all.
        due = sum(
            1
            for delay in reminder_delays()
            if now >= attempt.created_at + delay
        )
        if due <= attempt.reminders_sent:
            return None
        if not dry_run:
            if SignInAttempt.objects.filter(
                pk=attempt.pk, confirmed_at__isnull=False
            ).exists():
                return None  # confirmed since the list was loaded
            with translation.override(
                attempt.language or settings.LANGUAGE_CODE
            ):
                send_magic_link(
                    None,
                    attempt.user,
                    attempt=attempt,
                    drop_on=timezone.localdate(drop_at),
                    base_url=settings.SITE_URL,
                )
            attempt.reminders_sent = due
            attempt.last_reminded_at = now
            attempt.save(update_fields=["reminders_sent", "last_reminded_at"])
        return "reminded"
