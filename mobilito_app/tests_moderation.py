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

import tempfile

from django.contrib.admin.models import LogEntry
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from authentication.models import MobilitoUser, SignInAttempt
from core.lifecycle import TransitionError, moderate, public_generation
from core.models import ModerationState, PublicationState
from mobilito_app.models import (
    FlagReason,
    InfrastructureMedia,
    InfrastructureObservation,
    ModalShareSession,
    ModerationFlag,
)
from mobilito_app.tests_browse import STREET_BBOX, count_at, report_at

State = PublicationState


def validated(email):
    user = MobilitoUser.objects.create_user(email)
    user.email_validated = True
    user.save()
    return user


def staff(email="mod@example.com", superuser=True):
    user = validated(email)
    user.is_staff = True
    user.is_superuser = superuser
    user.save()
    return user


def add_photo(report, published=True):
    item = InfrastructureMedia(observation=report, published=published)
    item.file.save("p.jpg", ContentFile(b"\xff\xd8\xff\xe0jpeg"), save=False)
    item.save()
    return item


class TransitionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.author = validated("author@example.com")

    def test_publish_validated_submission(self):
        report = report_at(
            47.21, -1.55, state=State.PENDING_MODERATION, user=self.author
        )
        self.assertTrue(moderate(report, State.PUBLISHED))
        report.refresh_from_db()
        self.assertEqual(report.publication_state, State.PUBLISHED)
        self.assertEqual(report.moderation_state, ModerationState.CLEARED)
        self.assertFalse(moderate(report, State.PUBLISHED))

    def test_never_publish_unvalidated_or_ownerless(self):
        unvalidated = MobilitoUser.objects.create_user("new@example.com")
        attempt = SignInAttempt.objects.create(
            email="p@example.com", user=unvalidated
        )
        for user, attempt_ in ((unvalidated, None), (None, attempt)):
            report = report_at(
                47.21,
                -1.55,
                state=State.PENDING_VALIDATION,
                user=user,
                sign_in_attempt=attempt_,
            )
            with self.assertRaises(TransitionError):
                moderate(report, State.PUBLISHED)
            report.refresh_from_db()
            self.assertEqual(
                report.publication_state, State.PENDING_VALIDATION
            )

    def test_never_publish_or_hold_an_unfinished_count(self):
        count = count_at(
            47.21, -1.55, state=State.DRAFT, finished=False, user=self.author
        )
        for target in (State.PUBLISHED, State.LIGHT_HOLD, State.SANDBOXED):
            with self.assertRaises(TransitionError):
                moderate(count, target)

    def test_sandbox_from_any_sent_state(self):
        for start in (
            State.PENDING_VALIDATION,
            State.PENDING_MODERATION,
            State.PUBLISHED,
            State.LIGHT_HOLD,
        ):
            report = report_at(47.21, -1.55, state=start)
            moderate(report, State.SANDBOXED)
            self.assertEqual(report.moderation_state, ModerationState.FLAGGED)
            report.refresh_from_db()
            self.assertEqual(report.publication_state, State.SANDBOXED)

    def test_light_hold_needs_what_publishing_needs(self):
        # Light hold is visible by link: never for an unvalidated or
        # ownerless observation.
        unvalidated = MobilitoUser.objects.create_user("new@example.com")
        for user in (None, unvalidated):
            report = report_at(
                47.21, -1.55, state=State.PENDING_VALIDATION, user=user
            )
            with self.assertRaises(TransitionError):
                moderate(report, State.LIGHT_HOLD)
        report = report_at(
            47.21, -1.55, state=State.SANDBOXED, user=self.author
        )
        moderate(report, State.LIGHT_HOLD)
        report.refresh_from_db()
        self.assertEqual(report.publication_state, State.LIGHT_HOLD)

    def test_not_a_moderation_target(self):
        report = report_at(47.21, -1.55)
        with self.assertRaises(TransitionError):
            moderate(report, State.DRAFT)

    def test_moderation_changes_cached_pins_at_once(self):
        report = report_at(47.21, -1.55, user=self.author)
        url = reverse("observations_geojson")
        query = {"bbox": STREET_BBOX, "zoom": 17}
        before = public_generation()
        self.assertEqual(
            len(self.client.get(url, query).json()["features"]), 1
        )
        with self.captureOnCommitCallbacks(execute=True):
            moderate(report, State.SANDBOXED)
        self.assertNotEqual(public_generation(), before)
        self.assertEqual(self.client.get(url, query).json()["features"], [])

    def test_validating_in_admin_promotes(self):
        user = MobilitoUser.objects.create_user("later@example.com")
        report = report_at(
            47.21, -1.55, state=State.PENDING_VALIDATION, user=user
        )
        self.client.force_login(staff())
        response = self.client.post(
            reverse(
                "admin:authentication_mobilitouser_change", args=[user.pk]
            ),
            {
                "email": user.email,
                "is_active": "on",
                "email_validated": "on",
                "preferred_language": "",
                "use_device_location": "on",
                "created_at": "",
            },
        )
        self.assertEqual(response.status_code, 302, response.content[:2000])
        report.refresh_from_db()
        self.assertEqual(report.publication_state, State.PENDING_MODERATION)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class VisibilityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.author = validated("author@example.com")
        self.report = report_at(
            47.21, -1.55, user=self.author, description="Blocked lane"
        )
        self.photo = add_photo(self.report)
        self.count = count_at(47.21, -1.55, user=self.author)
        self.urls = {
            "report": reverse("reports_detail", args=[self.report.pk]),
            "summary": reverse("reports_summary", args=[self.report.pk]),
            "photo": reverse(
                "reports_photo", args=[self.report.pk, self.photo.pk]
            ),
            "count": reverse("counts_detail", args=[self.count.pk]),
        }

    def set_state(self, state):
        InfrastructureObservation.objects.update(publication_state=state)
        ModalShareSession.objects.update(publication_state=state)

    def statuses(self):
        return {
            name: self.client.get(url).status_code
            for name, url in self.urls.items()
        }

    def test_light_hold_reachable_by_link_with_note(self):
        self.set_state(State.LIGHT_HOLD)
        self.assertEqual(set(self.statuses().values()), {200})
        self.assertContains(
            self.client.get(self.urls["report"]),
            "This report is being checked",
        )
        self.assertContains(
            self.client.get(self.urls["count"]), "This count is being checked"
        )

    def test_never_public_once_author_unconfirmed(self):
        # However the state got there (e.g. confirmation withdrawn in
        # the admin after publishing).
        for state in (State.PUBLISHED, State.LIGHT_HOLD):
            self.set_state(state)
            self.author.email_validated = False
            self.author.save()
            self.assertEqual(set(self.statuses().values()), {404})

    def test_withdrawn_or_deleted_author_off_map_list_and_history(self):
        ModalShareSession.objects.update(total_car=3)
        other = count_at(47.21, -1.55, user=validated("b@example.com"))
        history_url = reverse("counts_detail", args=[other.pk])

        def public():
            pins = self.client.get(
                reverse("observations_geojson"),
                {"bbox": STREET_BBOX, "zoom": 17},
            ).json()["features"]
            listing = self.client.get(reverse("observations"))
            history = self.client.get(history_url).context["history"]
            return (
                len(pins) > 0,
                self.urls["report"] in listing.content.decode(),
                self.count.pk in [row["count"].pk for row in history],
            )

        self.assertEqual(public(), (True, True, True))
        self.client.force_login(staff())
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse(
                    "admin:authentication_mobilitouser_change",
                    args=[self.author.pk],
                ),
                {
                    "email": self.author.email,
                    "is_active": "on",
                    "preferred_language": "",
                    "use_device_location": "on",
                },
            )
        self.client.logout()
        self.author.refresh_from_db()
        self.assertFalse(self.author.email_validated)
        pins, in_list, in_history = public()
        self.assertFalse(in_list)
        self.assertFalse(in_history)
        # The cached pins were dropped: only "other" is left.
        features = self.client.get(
            reverse("observations_geojson"),
            {"bbox": STREET_BBOX, "zoom": 17},
        ).json()["features"]
        self.assertEqual(
            [f["properties"].get("url") for f in features],
            [reverse("counts_detail", args=[other.pk])],
        )
        # An author deleted outright (user set to NULL): gone too.
        ModalShareSession.objects.filter(pk=other.pk).update(user=None)
        cache.clear()
        self.assertEqual(
            self.client.get(
                reverse("observations_geojson"),
                {"bbox": STREET_BBOX, "zoom": 17},
            ).json()["features"],
            [],
        )

    def test_light_hold_is_off_the_map_and_list(self):
        self.set_state(State.LIGHT_HOLD)
        pins = self.client.get(
            reverse("observations_geojson"),
            {"bbox": STREET_BBOX, "zoom": 17},
        ).json()["features"]
        self.assertEqual(pins, [])
        listing = self.client.get(reverse("observations"))
        self.assertNotContains(listing, self.urls["report"])
        self.assertNotContains(listing, self.urls["count"])

    def test_sandboxed_only_author_and_moderators(self):
        self.set_state(State.SANDBOXED)
        self.assertEqual(set(self.statuses().values()), {404})
        self.client.force_login(self.author)
        self.assertEqual(set(self.statuses().values()), {200})
        self.assertContains(
            self.client.get(self.urls["report"]), "isn't visible to others"
        )
        self.client.force_login(staff())
        self.assertEqual(set(self.statuses().values()), {200})
        response = self.client.get(self.urls["report"])
        self.assertContains(response, "Moderator view")
        self.assertContains(response, "Sandboxed")

    def test_staff_without_view_permission_is_public(self):
        self.set_state(State.SANDBOXED)
        self.client.force_login(staff(superuser=False))
        self.assertEqual(set(self.statuses().values()), {404})

    def test_moderator_sees_hidden_photo_marked(self):
        InfrastructureMedia.objects.update(published=False)
        self.client.force_login(staff())
        response = self.client.get(self.urls["report"])
        self.assertContains(response, self.urls["photo"])
        self.assertContains(response, "Hidden from the public")
        self.assertEqual(self.client.get(self.urls["photo"]).status_code, 200)

    def test_owner_sees_being_checked_again(self):
        self.set_state(State.LIGHT_HOLD)
        self.client.force_login(self.author)
        response = self.client.get(reverse("my_observations"))
        self.assertContains(response, "Being checked")

    def test_flag_links_only_for_others(self):
        response = self.client.get(self.urls["report"])
        self.assertContains(
            response, reverse("flag", args=["photo", self.photo.pk])
        )
        self.assertContains(
            response, reverse("flag", args=["report", self.report.pk])
        )
        self.client.force_login(self.author)
        self.assertNotContains(
            self.client.get(self.urls["report"]), "Report a problem"
        )

    def test_count_flag_only_with_a_typed_place_name(self):
        flag_url = reverse("flag", args=["count", self.count.pk])
        self.assertNotContains(self.client.get(self.urls["count"]), flag_url)
        self.count.location.user_entered_address = "Rue X"
        self.count.location.save()
        self.assertContains(self.client.get(self.urls["count"]), flag_url)


@override_settings(
    MEDIA_ROOT=tempfile.mkdtemp(), MODERATION_FLAG_AUTO_HOLD_REPORTERS=2
)
class FlagTests(TestCase):
    def setUp(self):
        cache.clear()
        self.author = validated("author@example.com")
        self.report = report_at(
            47.21, -1.55, user=self.author, description="Buy stuff"
        )
        self.photo = add_photo(self.report)
        self.report_url = reverse("flag", args=["report", self.report.pk])
        self.photo_url = reverse("flag", args=["photo", self.photo.pk])

    def flag(self, url=None, **data):
        return self.client.post(
            url or self.report_url, {"reason": "spam", "note": "", **data}
        )

    def test_form_page_works_without_javascript(self):
        response = self.client.get(self.report_url)
        self.assertContains(response, "What's the problem?")
        self.assertContains(response, "<html", html=False)
        response = self.flag(note="An advert")
        self.assertRedirects(
            response,
            self.report.get_absolute_url(),
            fetch_redirect_response=False,
        )
        flag = ModerationFlag.objects.get()
        self.assertEqual(flag.target, self.report)
        self.assertEqual(flag.reason, FlagReason.SPAM)
        self.assertEqual(flag.note, "An advert")
        self.assertIsNone(flag.reporter)
        page = self.client.get(self.report.get_absolute_url())
        self.assertContains(page, "take a look at what you reported")

    def test_htmx_gets_fragments(self):
        headers = {"HTTP_HX_REQUEST": "true"}
        response = self.client.get(self.photo_url, **headers)
        self.assertContains(response, 'hx-post="%s"' % self.photo_url)
        self.assertNotContains(response, "<html")
        response = self.client.post(
            self.photo_url, {"reason": "privacy"}, **headers
        )
        self.assertContains(response, "We'll take a look")
        self.assertEqual(ModerationFlag.objects.get().target, self.photo)

    def test_text_reasons_fit_text(self):
        response = self.client.get(self.report_url)
        self.assertContains(response, "personal details")
        self.assertNotContains(response, "number plate")
        self.assertContains(response, "Buy stuff")
        self.assertContains(self.client.get(self.photo_url), "number plate")

    def test_reason_is_required(self):
        response = self.flag(reason="")
        self.assertContains(
            response, "Choose what the problem is", status_code=400
        )
        self.assertFalse(ModerationFlag.objects.exists())

    def test_htmx_errors_swap_in(self):
        response = self.client.post(
            self.report_url, {"reason": ""}, HTTP_HX_REQUEST="true"
        )
        self.assertContains(response, "Choose what the problem is")

    def test_already_reported_says_so(self):
        self.flag()
        self.assertContains(
            self.client.get(self.report_url), "already reported this"
        )

    def test_honeypot_pretends(self):
        response = self.flag(hp_field="x")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ModerationFlag.objects.exists())

    @override_settings(RATE_LIMIT_FLAG=(1, 3600))
    def test_rate_limited_pretends(self):
        self.flag()
        self.client.post(self.photo_url, {"reason": "spam"})
        self.assertEqual(ModerationFlag.objects.count(), 1)

    def test_once_per_person(self):
        self.flag()
        self.assertContains(
            self.client.get(self.report_url), "already reported this"
        )
        self.flag()
        self.assertEqual(ModerationFlag.objects.count(), 1)
        user = validated("reader@example.com")
        self.client.force_login(user)
        self.flag()
        self.flag()
        self.assertEqual(
            ModerationFlag.objects.filter(reporter=user).count(), 1
        )

    def test_cannot_flag_what_you_cannot_see_or_own(self):
        hidden = report_at(47.21, -1.55, state=State.SANDBOXED)
        self.assertEqual(
            self.client.get(
                reverse("flag", args=["report", hidden.pk])
            ).status_code,
            404,
        )
        self.photo.published = False
        self.photo.save()
        self.assertEqual(self.client.get(self.photo_url).status_code, 404)
        self.assertEqual(
            self.client.get(reverse("flag", args=["thing", 1])).status_code,
            404,
        )
        self.client.force_login(self.author)
        self.assertEqual(self.client.get(self.report_url).status_code, 404)

    def test_cannot_flag_photo_of_hidden_report(self):
        self.report.publication_state = State.SANDBOXED
        self.report.save()
        self.assertEqual(self.client.get(self.photo_url).status_code, 404)

    def test_moderators_act_in_the_admin_not_here(self):
        self.client.force_login(staff())
        self.assertEqual(self.client.get(self.report_url).status_code, 404)

    def test_only_words_can_be_flagged(self):
        silent = report_at(47.21, -1.55, user=self.author)
        url = reverse("flag", args=["report", silent.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        count = count_at(47.21, -1.55, user=self.author)
        url = reverse("flag", args=["count", count.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_confirmed_reporters_put_count_on_light_hold(self):
        count = count_at(47.21, -1.55, user=self.author, address="x")
        count.location.user_entered_address = "Rude words"
        count.location.save()
        url = reverse("flag", args=["count", count.pk])
        for email in ("a@example.com", "b@example.com"):
            self.client.force_login(validated(email))
            self.flag(url)
        count.refresh_from_db()
        self.assertEqual(count.publication_state, State.LIGHT_HOLD)

    def test_cannot_flag_unfinished_count(self):
        count = count_at(47.21, -1.55, finished=False)
        url = reverse("flag", args=["count", count.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_confirmed_reporters_put_report_on_light_hold(self):
        # Anonymous flags go to the queue but don't hide anything.
        for _ in range(3):
            self.client.logout()
            self.client.cookies.clear()
            self.flag()
        self.report.refresh_from_db()
        self.assertEqual(self.report.publication_state, State.PUBLISHED)
        for email in ("a@example.com", "b@example.com"):
            self.client.force_login(validated(email))
            self.flag()
        self.report.refresh_from_db()
        self.assertEqual(self.report.publication_state, State.LIGHT_HOLD)

    def test_repeated_photo_flags_hide_the_photo_only(self):
        for email in ("a@example.com", "b@example.com"):
            self.client.force_login(validated(email))
            self.flag(self.photo_url, reason="privacy")
        self.photo.refresh_from_db()
        self.assertFalse(self.photo.published)
        self.assertEqual(self.photo.moderation_state, ModerationState.FLAGGED)
        self.report.refresh_from_db()
        self.assertEqual(self.report.publication_state, State.PUBLISHED)

    @override_settings(MODERATION_FLAG_AUTO_HOLD_REPORTERS=0)
    def test_auto_hold_can_be_turned_off(self):
        for email in ("a@example.com", "b@example.com"):
            self.client.force_login(validated(email))
            self.flag()
        self.report.refresh_from_db()
        self.assertEqual(self.report.publication_state, State.PUBLISHED)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class AdminTests(TestCase):
    def setUp(self):
        cache.clear()
        self.moderator = staff()
        self.client.force_login(self.moderator)
        self.author = validated("author@example.com")
        self.changelist = reverse(
            "admin:mobilito_app_infrastructureobservation_changelist"
        )

    def change_url(self, report):
        return reverse(
            "admin:mobilito_app_infrastructureobservation_change",
            args=[report.pk],
        )

    def photo_form(self, report, photo, published):
        flags = "mobilito_app-moderationflag-content_type-object_id"
        data = {
            "media-TOTAL_FORMS": "1" if photo else "0",
            "media-INITIAL_FORMS": "1" if photo else "0",
            "media-MIN_NUM_FORMS": "0",
            "media-MAX_NUM_FORMS": "1000",
            f"{flags}-TOTAL_FORMS": "0",
            f"{flags}-INITIAL_FORMS": "0",
        }
        if photo:
            data["media-0-id"] = str(photo.pk)
            data["media-0-observation"] = str(report.pk)
            if published:
                data["media-0-published"] = "on"
        return data

    def act(self, action, *observations, url=None):
        return self.client.post(
            url or self.changelist,
            {
                "action": action,
                "_selected_action": [o.pk for o in observations],
            },
            follow=True,
        )

    def test_changelist_filters_and_pages_load(self):
        report = report_at(
            47.21, -1.55, state=State.PENDING_MODERATION, user=self.author
        )
        add_photo(report)
        count = count_at(47.21, -1.55, user=self.author)
        ModerationFlag.objects.create(target=report, reason="spam")
        for url in (
            self.changelist + "?publication_state__exact=pending_moderation",
            self.changelist + "?flagged=yes",
            self.changelist + "?flagged=no",
            reverse(
                "admin:mobilito_app_infrastructureobservation_change",
                args=[report.pk],
            ),
            reverse("admin:mobilito_app_modalsharesession_changelist"),
            reverse(
                "admin:mobilito_app_modalsharesession_change", args=[count.pk]
            ),
            reverse("admin:mobilito_app_moderationflag_changelist"),
            reverse("admin:mobilito_app_infrastructuretag_changelist"),
            reverse("admin:mobilito_app_contactmethod_changelist"),
        ):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        flagged = self.client.get(self.changelist + "?flagged=yes")
        self.assertEqual(flagged.context["cl"].result_count, 1)
        report_at(47.21, -1.55, user=self.author)
        unflagged = self.client.get(self.changelist + "?flagged=no")
        self.assertNotIn(
            report.pk, [o.pk for o in unflagged.context["cl"].result_list]
        )
        self.assertEqual(unflagged.context["cl"].result_count, 1)

    def test_photo_flag_counts_as_open_flag_on_report(self):
        report = report_at(47.21, -1.55, user=self.author)
        ModerationFlag.objects.create(target=add_photo(report), reason="spam")
        report_at(47.21, -1.55, user=self.author)
        flagged = self.client.get(self.changelist + "?flagged=yes")
        self.assertEqual(
            [o.pk for o in flagged.context["cl"].result_list], [report.pk]
        )

    def test_bulk_publish_skips_unvalidated_and_logs(self):
        good = report_at(
            47.21, -1.55, state=State.PENDING_MODERATION, user=self.author
        )
        unvalidated = MobilitoUser.objects.create_user("new@example.com")
        bad = report_at(
            47.21, -1.55, state=State.PENDING_VALIDATION, user=unvalidated
        )
        response = self.act("publish", good, bad)
        self.assertContains(response, "Published: 1")
        self.assertContains(response, "Not changed")
        good.refresh_from_db()
        bad.refresh_from_db()
        self.assertEqual(good.publication_state, State.PUBLISHED)
        self.assertEqual(bad.publication_state, State.PENDING_VALIDATION)
        self.assertTrue(
            LogEntry.objects.filter(object_id=str(good.pk)).exists()
        )

    def test_publish_resolves_flags_so_they_dont_rehold(self):
        report = report_at(
            47.21, -1.55, state=State.LIGHT_HOLD, user=self.author
        )
        photo = add_photo(report)
        ModerationFlag.objects.create(target=report, reason="spam")
        ModerationFlag.objects.create(target=photo, reason="spam")
        self.act("publish", report)
        self.assertFalse(
            ModerationFlag.objects.filter(resolved_at__isnull=True).exists()
        )
        self.assertEqual(
            ModerationFlag.objects.filter(resolved_by=self.moderator).count(),
            2,
        )

    def test_sandbox_resolves_flags_light_hold_keeps_them(self):
        report = report_at(47.21, -1.55, user=self.author)
        photo = add_photo(report, published=False)
        ModerationFlag.objects.create(target=report, reason="spam")
        ModerationFlag.objects.create(target=photo, reason="privacy")
        self.act("light_hold", report)
        self.assertEqual(
            ModerationFlag.objects.filter(resolved_at__isnull=True).count(), 2
        )
        self.act("sandbox", report)
        self.assertFalse(
            ModerationFlag.objects.filter(resolved_at__isnull=True).exists()
        )

    def test_hold_and_sandbox_counts(self):
        count = count_at(47.21, -1.55, user=self.author)
        url = reverse("admin:mobilito_app_modalsharesession_changelist")
        self.act("light_hold", count, url=url)
        count.refresh_from_db()
        self.assertEqual(count.publication_state, State.LIGHT_HOLD)
        self.act("sandbox", count, url=url)
        count.refresh_from_db()
        self.assertEqual(count.publication_state, State.SANDBOXED)

    def test_no_deleting_or_editing_state(self):
        report = report_at(47.21, -1.55, user=self.author)
        url = reverse(
            "admin:mobilito_app_infrastructureobservation_delete",
            args=[report.pk],
        )
        self.assertEqual(self.client.get(url).status_code, 403)
        change = self.client.get(
            reverse(
                "admin:mobilito_app_infrastructureobservation_change",
                args=[report.pk],
            )
        )
        self.assertNotContains(change, 'name="publication_state"')

    def test_reshowing_a_photo_resolves_its_flags(self):
        report = report_at(47.21, -1.55, user=self.author)
        photo = add_photo(report, published=False)
        ModerationFlag.objects.create(target=photo, reason="privacy")
        url = reverse(
            "admin:mobilito_app_infrastructureobservation_change",
            args=[report.pk],
        )
        prefix = "media"
        data = {
            f"{prefix}-TOTAL_FORMS": "1",
            f"{prefix}-INITIAL_FORMS": "1",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
            f"{prefix}-0-id": str(photo.pk),
            f"{prefix}-0-observation": str(report.pk),
            f"{prefix}-0-published": "on",
            f"{prefix}-0-moderation_state": "cleared",
            "mobilito_app-moderationflag-content_type-object_id-TOTAL_FORMS": (
                "0"
            ),
            "mobilito_app-moderationflag-content_type-object_id-"
            "INITIAL_FORMS": "0",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302, response.content[:3000])
        photo.refresh_from_db()
        self.assertTrue(photo.published)
        self.assertEqual(photo.moderation_state, ModerationState.CLEARED)
        self.assertIsNotNone(ModerationFlag.objects.get().resolved_at)

    def test_hiding_a_photo_in_admin(self):
        report = report_at(47.21, -1.55, user=self.author)
        photo = add_photo(report)
        ModerationFlag.objects.create(target=photo, reason="privacy")
        response = self.client.post(
            self.change_url(report), self.photo_form(report, photo, False)
        )
        self.assertEqual(response.status_code, 302)
        photo.refresh_from_db()
        self.assertFalse(photo.published)
        self.assertEqual(photo.moderation_state, ModerationState.FLAGGED)
        self.assertIsNotNone(ModerationFlag.objects.get().resolved_at)
        self.client.logout()
        url = reverse("reports_photo", args=[report.pk, photo.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_change_form_buttons(self):
        report = report_at(
            47.21, -1.55, state=State.PENDING_MODERATION, user=self.author
        )
        photo = add_photo(report)
        page = self.client.get(self.change_url(report))
        self.assertContains(page, 'name="_moderate_publish"')
        self.assertContains(page, 'name="_moderate_sandbox"')
        # The photo was hidden automatically after the page loaded:
        # its stale "published" box mustn't show it again.
        InfrastructureMedia.objects.filter(pk=photo.pk).update(published=False)
        data = self.photo_form(report, photo, True)
        data["_moderate_publish"] = "Publish"
        response = self.client.post(self.change_url(report), data)
        self.assertRedirects(response, self.change_url(report))
        photo.refresh_from_db()
        self.assertFalse(photo.published)
        self.assertEqual(
            LogEntry.objects.filter(object_id=str(report.pk)).count(), 1
        )
        report.refresh_from_db()
        self.assertEqual(report.publication_state, State.PUBLISHED)
        page = self.client.get(self.change_url(report))
        self.assertNotContains(page, 'name="_moderate_publish"')

    def test_change_form_button_refuses_unvalidated(self):
        unvalidated = MobilitoUser.objects.create_user("new@example.com")
        report = report_at(
            47.21, -1.55, state=State.PENDING_VALIDATION, user=unvalidated
        )
        data = self.photo_form(report, None, True)
        data["_moderate_light_hold"] = "Light hold"
        response = self.client.post(self.change_url(report), data, follow=True)
        self.assertContains(response, "Not changed")
        report.refresh_from_db()
        self.assertEqual(report.publication_state, State.PENDING_VALIDATION)

    def test_view_only_staff_get_no_buttons(self):
        from django.contrib.auth.models import Permission

        viewer = staff("viewer@example.com", superuser=False)
        viewer.user_permissions.add(
            Permission.objects.get(codename="view_infrastructureobservation")
        )
        self.client.force_login(viewer)
        report = report_at(
            47.21, -1.55, state=State.PENDING_MODERATION, user=self.author
        )
        page = self.client.get(self.change_url(report))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "_moderate_")
        data = self.photo_form(report, None, True)
        data["_moderate_publish"] = "Publish"
        response = self.client.post(self.change_url(report), data)
        self.assertEqual(response.status_code, 403)
        report.refresh_from_db()
        self.assertEqual(report.publication_state, State.PENDING_MODERATION)

    def test_count_page_has_only_decisions(self):
        count = count_at(
            47.21, -1.55, state=State.PENDING_MODERATION, user=self.author
        )
        page = self.client.get(
            reverse(
                "admin:mobilito_app_modalsharesession_change", args=[count.pk]
            )
        )
        self.assertContains(page, 'name="_moderate_publish"')
        self.assertNotContains(page, 'name="_save"')
        self.assertNotContains(page, "untick its")

    def test_flag_status_filter(self):
        report = report_at(47.21, -1.55, user=self.author)
        ModerationFlag.objects.create(target=report, reason="spam")
        done = ModerationFlag.objects.create(
            target=report, reason="spam", resolved_at=timezone.now()
        )
        url = reverse("admin:mobilito_app_moderationflag_changelist")
        still_open = self.client.get(url + "?status=open")
        self.assertEqual(still_open.context["cl"].result_count, 1)
        dealt = self.client.get(url + "?status=done")
        self.assertEqual(
            [f.pk for f in dealt.context["cl"].result_list], [done.pk]
        )

    def test_mark_flags_resolved(self):
        report = report_at(47.21, -1.55, user=self.author)
        flag = ModerationFlag.objects.create(target=report, reason="spam")
        self.act(
            "mark_resolved",
            flag,
            url=reverse("admin:mobilito_app_moderationflag_changelist"),
        )
        flag.refresh_from_db()
        self.assertEqual(flag.resolved_by, self.moderator)
