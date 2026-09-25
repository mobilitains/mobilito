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

import io
import re
import smtplib
import tempfile
import threading
import time
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user
from django.contrib.gis.geos import Point
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db.models import ProtectedError
from django.core import mail, signing
from django.core.cache import cache
from django.db import connection, transaction
from django.test import (
    RequestFactory,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.urls import reverse
from django.utils import timezone, translation
from sesame.utils import get_token

from authentication.models import (
    MobilitoUser,
    SignInAttempt,
    get_user_by_email,
    normalise_email,
)
from authentication.provisional import (
    ATTEMPT_SESSION_KEY,
    drop_attempt,
    get_observer,
)
from authentication.tokens import attempt_from_token
from core.models import Location, LocationEvidence, PublicationState
from mobilito_app.models import (
    InfrastructureObservation,
    Mode,
    ModalShareCountEvent,
    ModalShareSession,
    ModerationFlag,
    ObservationAction,
)


class NormaliseEmailTests(TestCase):
    def test_lowercases(self):
        self.assertEqual(
            normalise_email("Test@Example.COM"), "test@example.com"
        )

    def test_already_lowercase_unchanged(self):
        self.assertEqual(
            normalise_email("test@example.com"), "test@example.com"
        )


class MobilitoUserManagerTests(TestCase):
    def test_create_user_stores_normalised_email(self):
        user = MobilitoUser.objects.create_user("Test@Example.COM")
        self.assertEqual(user.email, "test@example.com")

    def test_new_user_is_not_validated(self):
        user = MobilitoUser.objects.create_user("new@example.com")
        self.assertFalse(user.email_validated)

    def test_new_user_is_active(self):
        user = MobilitoUser.objects.create_user("active@example.com")
        self.assertTrue(user.is_active)

    def test_new_user_has_no_stored_language_preference(self):
        # None = fall back to Accept-Language (§7), not any real choice.
        user = MobilitoUser.objects.create_user("lang@example.com")
        self.assertIsNone(user.preferred_language)

    def test_new_user_uses_device_location_by_default(self):
        user = MobilitoUser.objects.create_user("geo@example.com")
        self.assertTrue(user.use_device_location)

    def test_create_user_idempotent(self):
        user1 = MobilitoUser.objects.create_user("dup@example.com")
        user2 = MobilitoUser.objects.create_user("dup@example.com")
        self.assertEqual(user1.pk, user2.pk)

    def test_create_superuser_sets_flags(self):
        user = MobilitoUser.objects.create_superuser("admin@example.com")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_get_by_natural_key(self):
        created = MobilitoUser.objects.create_user("nat@example.com")
        fetched = MobilitoUser.objects.get_by_natural_key("nat@example.com")
        self.assertEqual(created.pk, fetched.pk)

    def test_get_by_natural_key_normalises_email(self):
        created = MobilitoUser.objects.create_user("nat2@example.com")
        fetched = MobilitoUser.objects.get_by_natural_key("NAT2@EXAMPLE.COM")
        self.assertEqual(created.pk, fetched.pk)


class GetUserByEmailTests(TestCase):
    def test_creates_user_if_not_exists(self):
        user = get_user_by_email("brand-new@example.com")
        self.assertIsNotNone(user.pk)

    def test_returns_existing_user(self):
        user1 = get_user_by_email("existing@example.com")
        user2 = get_user_by_email("existing@example.com")
        self.assertEqual(user1.pk, user2.pk)


VERIFY_LINK_RE = re.compile(r"http://testserver(/auth/verify/[^\s<>\"]+)")


def link_path_from_outbox(index: int = -1) -> str:
    """Return the path (with query) of the magic link in an email."""
    match = VERIFY_LINK_RE.search(mail.outbox[index].body)
    return match.group(1)


class MagicLinkStartTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_get_renders_form(self):
        response = self.client.get(reverse("auth_start"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="email"')

    def test_get_keeps_safe_next(self):
        response = self.client.get(
            reverse("auth_start"), {"next": "/counts/new/"}
        )
        self.assertContains(response, 'value="/counts/new/"')

    def test_get_drops_unsafe_next(self):
        response = self.client.get(
            reverse("auth_start"), {"next": "https://evil.example/"}
        )
        # (The nav's language form echoes the full URL; set_language
        # validates it separately.)
        self.assertEqual(response.context["form"]["next"].value(), "")

    def test_post_creates_user_and_sends_link(self):
        response = self.client.post(
            reverse("auth_start"), {"email": "New@Example.com"}
        )
        self.assertRedirects(response, reverse("auth_sent"))
        user = MobilitoUser.objects.get(email="new@example.com")
        self.assertFalse(user.email_validated)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["new@example.com"])
        self.assertNotIn("\n", mail.outbox[0].subject)
        self.assertTrue(link_path_from_outbox().startswith("/auth/verify/"))
        html, mimetype = mail.outbox[0].alternatives[0]
        self.assertEqual(mimetype, "text/html")
        self.assertIn("/auth/verify/", html)

    def test_send_failure_shows_friendly_error(self):
        with mock.patch(
            "authentication.views.send_magic_link",
            side_effect=smtplib.SMTPException("down"),
        ):
            response = self.client.post(
                reverse("auth_start"), {"email": "fail@example.com"}
            )
        self.assertEqual(response.status_code, 503)
        self.assertContains(
            response, "couldn&#x27;t send the email", status_code=503
        )

    def test_sent_page_without_session_email(self):
        response = self.client.get(reverse("auth_sent"))
        self.assertContains(response, "sent you a sign-in link")

    def test_request_language_is_stored_once_the_link_is_used(self):
        self.client.post(
            reverse("auth_start"),
            {"email": "lang@example.com"},
            HTTP_ACCEPT_LANGUAGE="en",
        )
        user = MobilitoUser.objects.get(email="lang@example.com")
        # Not before: whoever typed the address hasn't proved it's
        # theirs (§5.4).
        self.assertIsNone(user.preferred_language)
        path = link_path_from_outbox()
        self.assertIn("lang=en", path)
        # Opened on a device set to French: the request's language
        # still wins.
        self.client.post(path, {"remember": "on"}, HTTP_ACCEPT_LANGUAGE="fr")
        user.refresh_from_db()
        self.assertEqual(user.preferred_language, "en")

    def test_tampered_language_is_ignored(self):
        self.client.post(reverse("auth_start"), {"email": "lang2@example.com"})
        path = link_path_from_outbox().replace("lang=fr", "lang=xx")
        self.client.post(path, {"remember": "on"})
        user = MobilitoUser.objects.get(email="lang2@example.com")
        self.assertIsNone(user.preferred_language)

    def test_request_keeps_existing_language_preference(self):
        user = MobilitoUser.objects.create_user("keep@example.com")
        user.preferred_language = "fr"
        user.save()
        self.client.post(
            reverse("auth_start"),
            {"email": "keep@example.com"},
            HTTP_ACCEPT_LANGUAGE="en",
        )
        self.assertNotIn("lang=", link_path_from_outbox())
        self.client.post(link_path_from_outbox(), {"remember": "on"})
        user.refresh_from_db()
        self.assertEqual(user.preferred_language, "fr")

    def test_get_prefills_email(self):
        response = self.client.get(
            reverse("auth_start"), {"email": "pre@example.com"}
        )
        self.assertContains(response, 'value="pre@example.com"')

    def test_sent_page_shows_address(self):
        response = self.client.post(
            reverse("auth_start"), {"email": "shown@example.com"}, follow=True
        )
        self.assertContains(response, "shown@example.com")

    def test_existing_user_is_reused(self):
        existing = MobilitoUser.objects.create_user("again@example.com")
        self.client.post(reverse("auth_start"), {"email": "again@example.com"})
        self.assertEqual(
            MobilitoUser.objects.filter(email="again@example.com").count(), 1
        )
        self.assertEqual(mail.outbox[0].to, [existing.email])

    def test_invalid_email_rerenders_form(self):
        response = self.client.post(
            reverse("auth_start"), {"email": "not-an-email"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-invalid="true"')
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(MobilitoUser.objects.exists())

    def test_honeypot_pretends_success_but_does_nothing(self):
        response = self.client.post(
            reverse("auth_start"),
            {"email": "bot@example.com", "hp_field": "http://spam"},
        )
        self.assertRedirects(response, reverse("auth_sent"))
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(MobilitoUser.objects.exists())

    def test_inactive_user_gets_no_email(self):
        user = MobilitoUser.objects.create_user("gone@example.com")
        user.is_active = False
        user.save()
        response = self.client.post(
            reverse("auth_start"), {"email": "gone@example.com"}
        )
        self.assertRedirects(response, reverse("auth_sent"))
        self.assertEqual(len(mail.outbox), 0)

    def test_next_is_carried_in_link(self):
        self.client.post(
            reverse("auth_start"),
            {"email": "next@example.com", "next": "/counts/new/"},
        )
        self.assertIn("next=%2Fcounts%2Fnew%2F", link_path_from_outbox())

    def test_unsafe_next_is_not_carried_in_link(self):
        self.client.post(
            reverse("auth_start"),
            {"email": "next@example.com", "next": "https://evil.example/"},
        )
        self.assertNotIn("next=", link_path_from_outbox())

    @override_settings(RATE_LIMIT_AUTH_START_PER_EMAIL=(1, 3600))
    def test_rate_limited_per_email(self):
        self.client.post(reverse("auth_start"), {"email": "rl@example.com"})
        response = self.client.post(
            reverse("auth_start"), {"email": "RL@example.com"}
        )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(RATE_LIMIT_AUTH_START_PER_IP=(2, 3600))
    def test_rate_limited_per_ip(self):
        for i in range(2):
            self.client.post(
                reverse("auth_start"), {"email": f"ip{i}@example.com"}
            )
        response = self.client.post(
            reverse("auth_start"), {"email": "ip2@example.com"}
        )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(mail.outbox), 2)

    def test_email_uses_users_preferred_language(self):
        user = MobilitoUser.objects.create_user("en@example.com")
        user.preferred_language = "en"
        user.save()
        self.client.post(
            reverse("auth_start"),
            {"email": "en@example.com"},
            HTTP_ACCEPT_LANGUAGE="fr",
        )
        html, _mimetype = mail.outbox[0].alternatives[0]
        self.assertIn('<html lang="en">', html)


class MagicLinkVerifyTests(TestCase):
    def setUp(self):
        self.user = MobilitoUser.objects.create_user("verify@example.com")
        self.url = reverse("auth_verify", args=[get_token(self.user)])

    def test_get_shows_confirmation_without_signing_in(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "verify@example.com")
        self.assertFalse(get_user(self.client).is_authenticated)
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_validated)
        # A prefetching link scanner mustn't consume the token.
        self.assertIsNone(self.user.last_login)

    def test_get_with_bad_token(self):
        response = self.client.get(
            reverse("auth_verify", args=["not-a-token"])
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, reverse("auth_start"), status_code=400)

    def test_post_signs_in_and_validates(self):
        response = self.client.post(self.url, {"remember": "on"})
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(get_user(self.client).pk, self.user.pk)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_validated)
        self.assertFalse(self.client.session.get_expire_at_browser_close())

    def test_post_without_remember_expires_at_browser_close(self):
        self.client.post(self.url, {})
        self.assertTrue(get_user(self.client).is_authenticated)
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_link_works_only_once(self):
        self.client.post(self.url, {"remember": "on"})
        self.client.logout()
        response = self.client.post(self.url, {"remember": "on"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(get_user(self.client).is_authenticated)

    def test_post_redirects_to_safe_next(self):
        response = self.client.post(
            self.url + "?next=/counts/new/", {"remember": "on"}
        )
        self.assertEqual(response["Location"], "/counts/new/")

    def test_post_ignores_non_path_next(self):
        response = self.client.post(self.url + "?next=foo", {"remember": "on"})
        self.assertRedirects(response, reverse("home"))

    def test_post_ignores_unsafe_next(self):
        response = self.client.post(
            self.url + "?next=https://evil.example/", {"remember": "on"}
        )
        self.assertRedirects(response, reverse("home"))

    def test_reused_link_when_already_signed_in_just_continues(self):
        self.client.post(self.url, {"remember": "on"})
        response = self.client.post(
            self.url + "?next=/counts/new/", {"remember": "on"}
        )
        self.assertEqual(response["Location"], "/counts/new/")
        self.assertEqual(get_user(self.client).pk, self.user.pk)

    def test_invalid_link_page_keeps_next(self):
        response = self.client.get(
            reverse("auth_verify", args=["bad"]) + "?next=/counts/new/"
        )
        self.assertContains(response, "?next=/counts/new/", status_code=400)

    def test_inactive_user_cannot_sign_in(self):
        self.user.is_active = False
        self.user.save()
        response = self.client.post(self.url, {"remember": "on"})
        self.assertEqual(response.status_code, 400)

    def test_round_trip_from_start(self):
        cache.clear()
        self.client.post(
            reverse("auth_start"),
            {"email": "trip@example.com", "next": "/counts/new/"},
        )
        path = link_path_from_outbox()
        self.assertEqual(self.client.get(path).status_code, 200)
        response = self.client.post(path, {"remember": "on"})
        self.assertEqual(response["Location"], "/counts/new/")
        user = MobilitoUser.objects.get(email="trip@example.com")
        self.assertTrue(user.email_validated)
        self.assertEqual(get_user(self.client).pk, user.pk)


class LogoutTests(TestCase):
    def test_get_not_allowed(self):
        response = self.client.get(reverse("auth_logout"))
        self.assertEqual(response.status_code, 405)

    def test_post_signs_out(self):
        self.client.force_login(
            MobilitoUser.objects.create_user("out@example.com")
        )
        response = self.client.post(reverse("auth_logout"))
        self.assertRedirects(response, reverse("home"))
        self.assertFalse(get_user(self.client).is_authenticated)


class UnvalidatedBannerTests(TestCase):
    BANNER_TEXT = "Please confirm your email address"

    def test_shown_to_unvalidated_user(self):
        self.client.force_login(
            MobilitoUser.objects.create_user("unval@example.com")
        )
        response = self.client.get(reverse("home"))
        self.assertContains(response, self.BANNER_TEXT)
        self.assertContains(response, 'value="unval@example.com"')

    def test_hidden_from_validated_user(self):
        user = MobilitoUser.objects.create_user("val@example.com")
        user.email_validated = True
        user.save()
        self.client.force_login(user)
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, self.BANNER_TEXT)

    def test_hidden_from_anonymous_visitor(self):
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, self.BANNER_TEXT)

    def test_never_says_account(self):
        self.client.force_login(
            MobilitoUser.objects.create_user("acct@example.com")
        )
        response = self.client.get(reverse("home"))
        self.assertNotIn("account", response.content.decode().lower())


# --- Provisional sign-in (design §5.4) ---------------------------------


def make_location(lon=-1.5536, lat=47.2184):
    return Location.objects.create(point=Point(lon, lat))


def make_session(**owner):
    return ModalShareSession.objects.create(
        location=make_location(),
        started_at=timezone.now(),
        publication_state=PublicationState.PENDING_VALIDATION,
        **owner,
    )


def attempt_link_from_outbox(index=-1):
    return link_path_from_outbox(index)


def attempt_in_link(path):
    """The attempt an emailed confirmation link names."""
    token = path.split("/auth/verify/")[1].split("/")[0]
    return attempt_from_token(token)


class ObserveViewTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_get_renders_form(self):
        response = self.client.get(
            reverse("auth_observe"), {"next": "/counts/new/"}
        )
        self.assertContains(response, 'value="/counts/new/"')

    def test_post_starts_attempt_without_logging_in(self):
        response = self.client.post(
            reverse("auth_observe"),
            {"email": "Walker@Example.com", "next": "/counts/new/"},
        )
        self.assertEqual(response["Location"], "/counts/new/")
        attempt = SignInAttempt.objects.get()
        self.assertEqual(attempt.email, "walker@example.com")
        self.assertEqual(self.client.session[ATTEMPT_SESSION_KEY], attempt.pk)
        self.assertFalse(get_user(self.client).is_authenticated)
        path = attempt_link_from_outbox()
        self.assertEqual(attempt_in_link(path), attempt)
        self.assertIn("next=%2Fcounts%2Fnew%2F", path)
        self.assertIn("Confirm your email address", mail.outbox[0].subject)

    def test_known_observer_skips_the_form(self):
        self.client.post(reverse("auth_observe"), {"email": "a@example.com"})
        response = self.client.get(
            reverse("auth_observe"), {"next": "/counts/new/"}
        )
        self.assertEqual(response["Location"], "/counts/new/")

    def test_honeypot_creates_nothing(self):
        self.client.post(
            reverse("auth_observe"),
            {"email": "bot@example.com", "hp_field": "x"},
        )
        self.assertFalse(SignInAttempt.objects.exists())
        self.assertEqual(len(mail.outbox), 0)
        self.assertNotIn(ATTEMPT_SESSION_KEY, self.client.session)

    def test_invalid_email(self):
        response = self.client.post(reverse("auth_observe"), {"email": "nope"})
        self.assertContains(response, 'aria-invalid="true"')
        self.assertFalse(SignInAttempt.objects.exists())

    @override_settings(RATE_LIMIT_AUTH_START_PER_EMAIL=(1, 3600))
    def test_rate_limited_per_email(self):
        self.client.post(reverse("auth_observe"), {"email": "r@example.com"})
        self.client.session.flush()
        other = self.client_class()
        response = other.post(
            reverse("auth_observe"), {"email": "r@example.com"}
        )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(SignInAttempt.objects.count(), 1)

    def test_deactivated_address_gets_nothing(self):
        user = MobilitoUser.objects.create_user("off@example.com")
        user.is_active = False
        user.save()
        response = self.client.post(
            reverse("auth_observe"), {"email": "off@example.com"}
        )
        self.assertRedirects(response, reverse("auth_sent"))
        self.assertFalse(SignInAttempt.objects.exists())
        self.assertEqual(len(mail.outbox), 0)


class ObserverScopeTests(TestCase):
    """A provisional session never reaches an existing user's data."""

    def setUp(self):
        cache.clear()
        self.owner = MobilitoUser.objects.create_user("owner@example.com")
        self.owner.email_validated = True
        self.owner.preferred_language = "fr"
        self.owner.save()
        self.theirs = make_session(user=self.owner)

    def _observer(self):
        request = RequestFactory().get("/")
        request.user = get_user(self.client)
        request.session = self.client.session
        return get_observer(request)

    def test_typing_someone_elses_address_reveals_nothing(self):
        self.client.post(
            reverse("auth_observe"), {"email": "owner@example.com"}
        )
        observer = self._observer()
        self.assertTrue(observer.is_provisional)
        self.assertIsNone(observer.user)
        self.assertFalse(observer.owns(self.theirs))
        self.assertFalse(
            observer.own(ModalShareSession.objects)
            .filter(pk=self.theirs.pk)
            .exists()
        )
        mine = make_session(**observer.owner_fields())
        self.assertTrue(observer.owns(mine))
        self.assertIsNone(mine.user)

    def test_owner_does_not_see_unconfirmed_attempt_data(self):
        attempt = SignInAttempt.objects.create(
            email=self.owner.email, user=self.owner
        )
        impostor = make_session(user=None, sign_in_attempt=attempt)
        self.client.force_login(self.owner)
        observer = self._observer()
        self.assertFalse(observer.owns(impostor))
        self.assertTrue(observer.owns(self.theirs))

    def test_language_switch_does_not_touch_existing_user(self):
        self.client.post(
            reverse("auth_observe"), {"email": "owner@example.com"}
        )
        self.client.post(
            reverse("set_language"), {"language": "en", "next": "/"}
        )
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.preferred_language, "fr")


class AttemptConfirmationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client.post(
            reverse("auth_observe"),
            {"email": "new@example.com", "next": "/counts/new/"},
        )
        self.attempt = SignInAttempt.objects.get()
        self.session = make_session(user=None, sign_in_attempt=self.attempt)
        self.path = attempt_link_from_outbox()

    def test_get_shows_attempt_wording_without_confirming(self):
        response = self.client.get(self.path)
        self.assertContains(response, "What you recorded will then count")
        self.attempt.refresh_from_db()
        self.assertFalse(self.attempt.is_confirmed)

    def test_post_confirms_and_attaches_data(self):
        response = self.client.post(self.path, {"remember": "on"})
        self.assertEqual(response["Location"], "/counts/new/")
        self.attempt.refresh_from_db()
        self.assertTrue(self.attempt.is_confirmed)
        self.session.refresh_from_db()
        self.assertEqual(self.session.user, self.attempt.user)
        self.assertEqual(
            self.session.publication_state,
            PublicationState.PENDING_MODERATION,
        )
        self.assertTrue(get_user(self.client).is_authenticated)

    def test_confirming_elsewhere_works(self):
        other = self.client_class()
        other.post(self.path, {"remember": "on"})
        self.attempt.refresh_from_db()
        self.assertTrue(self.attempt.is_confirmed)
        # The original browser is not signed in by someone else's
        # click, but still sees what it recorded.
        self.assertFalse(get_user(self.client).is_authenticated)

    def test_tampered_token_is_refused(self):
        other_attempt = SignInAttempt.objects.create(
            email=self.attempt.email, user=self.attempt.user
        )
        token = self.path.split("/auth/verify/")[1].split("/")[0]
        forged = signing.dumps(
            {"attempt": other_attempt.pk, "user": other_attempt.user_id},
            salt="wrong",
        )
        response = self.client.post(
            self.path.replace(token, forged), {"remember": "on"}
        )
        self.assertEqual(response.status_code, 400)
        other_attempt.refresh_from_db()
        self.assertFalse(other_attempt.is_confirmed)

    def test_link_is_single_use(self):
        self.client.post(self.path, {"remember": "on"})
        self.client.logout()
        response = self.client.post(self.path, {"remember": "on"})
        # Not signed in again by a reused link; told it's done.
        self.assertContains(response, "Already confirmed")
        self.assertFalse(get_user(self.client).is_authenticated)

    def test_link_expires(self):
        with override_settings(SIGN_IN_ATTEMPT_LINK_MAX_AGE=-1):
            response = self.client.post(self.path, {"remember": "on"})
        self.assertEqual(response.status_code, 400)

    def test_other_sign_ins_do_not_kill_attempt_links(self):
        # Sesame's single-use tokens die whenever last_login changes;
        # attempt links must not (their data would then be dropped).
        other = self.client_class()
        other.post(reverse("auth_start"), {"email": self.attempt.email})
        other.post(link_path_from_outbox(), {"remember": "on"})
        response = self.client.post(self.path, {"remember": "on"})
        self.assertEqual(response["Location"], "/counts/new/")
        self.attempt.refresh_from_db()
        self.assertTrue(self.attempt.is_confirmed)

    def test_dropped_attempt_link_is_refused(self):
        self.session.delete()
        drop_attempt(self.attempt)
        response = self.client.post(self.path, {"remember": "on"})
        self.assertEqual(response.status_code, 400)

    def test_old_format_token_is_refused_not_a_crash(self):
        response = self.client.get(
            "/auth/verify/AAAA:abcdef:AAAAAAAAAAAAAAAAAAAAAAAAAAA/"
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.get(
            "/auth/verify/AAAA:abcdef:AAAAAAAAAAAAAAAAAAAAAAAAAAA/?attempt=1"
        )
        self.assertEqual(response.status_code, 400)

    def test_device_location_choice_carries_over(self):
        self.client.post(reverse("set_device_location"), {})
        self.client.post(self.path, {"remember": "on"})
        self.attempt.user.refresh_from_db()
        self.assertFalse(self.attempt.user.use_device_location)

    def test_attempt_token_is_not_a_plain_sign_in_link(self):
        plain = self.path.split("?")[0]
        response = self.client.post(plain, {"remember": "on"})
        self.assertEqual(response.status_code, 400)

    def test_plain_token_does_not_confirm_an_attempt(self):
        token = get_token(self.attempt.user)
        url = reverse("auth_verify", args=[token]) + "?attempt=1"
        response = self.client.post(url, {"remember": "on"})
        self.assertEqual(response.status_code, 400)

    def test_plain_sign_in_in_this_browser_confirms_its_attempt(self):
        # E.g. they tapped the nav's "Sign in" instead of the link.
        self.client.post(reverse("auth_start"), {"email": self.attempt.email})
        self.client.post(link_path_from_outbox(), {"remember": "on"})
        self.attempt.refresh_from_db()
        self.assertTrue(self.attempt.is_confirmed)
        self.session.refresh_from_db()
        self.assertEqual(self.session.user, self.attempt.user)

    def test_plain_sign_in_elsewhere_confirms_nothing(self):
        other = self.client_class()
        other.post(reverse("auth_start"), {"email": self.attempt.email})
        other.post(link_path_from_outbox(), {"remember": "on"})
        self.attempt.refresh_from_db()
        self.assertFalse(self.attempt.is_confirmed)

    def test_after_confirming_elsewhere_new_records_belong_to_user(self):
        other = self.client_class()
        other.post(self.path, {"remember": "on"})
        request = RequestFactory().get("/")
        request.user = get_user(self.client)
        request.session = self.client.session
        observer = get_observer(request)
        self.assertEqual(observer.owner_fields()["user"], self.attempt.user)

    def test_reopened_link_in_holding_browser_just_continues(self):
        other = self.client_class()
        other.post(self.path, {"remember": "on"})
        response = self.client.get(self.path)
        self.assertEqual(response["Location"], "/counts/new/")

    def test_reopened_link_elsewhere_says_already_confirmed(self):
        self.client.post(self.path, {"remember": "on"})
        third = self.client_class()
        response = third.get(self.path)
        self.assertContains(response, "Already confirmed")
        self.assertContains(response, "Sign in on this device")

    def test_change_context_survives_a_validation_error(self):
        response = self.client.post(
            reverse("auth_observe"), {"email": "bad", "change": "1"}
        )
        self.assertContains(response, "Change your email address")
        self.assertNotContains(response, "Sign in instead")

    def test_change_success_message(self):
        response = self.client.post(
            reverse("auth_observe"),
            {"email": "right@example.com", "change": "1", "next": "/"},
            follow=True,
        )
        self.assertContains(response, "sent a new link to right@example.com")

    def test_expired_link_for_unconfirmed_attempt_resends_it(self):
        response = self.client.get(
            reverse("auth_verify", args=["bad"]) + "?attempt=1"
        )
        self.assertContains(
            response, reverse("auth_observe_resend"), status_code=400
        )

    def test_changing_a_mistyped_address_keeps_the_records(self):
        response = self.client.get(
            reverse("auth_observe"), {"change": "1", "next": "/x/"}
        )
        self.assertContains(response, 'value="new@example.com"')
        self.client.post(
            reverse("auth_observe"),
            {"email": "right@example.com", "next": "/x/"},
        )
        self.assertFalse(
            SignInAttempt.objects.filter(pk=self.attempt.pk).exists()
        )
        new_attempt = SignInAttempt.objects.get(email="right@example.com")
        self.session.refresh_from_db()
        self.assertEqual(self.session.sign_in_attempt, new_attempt)
        self.assertEqual(
            self.client.session[ATTEMPT_SESSION_KEY], new_attempt.pk
        )
        self.assertEqual(mail.outbox[-1].to, ["right@example.com"])

    def test_banner_offers_change_of_address(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Not your address?")

    def test_deactivated_users_attempt_carries_nothing(self):
        self.attempt.user.is_active = False
        self.attempt.user.save()
        other = self.client_class()
        other.post(self.path, {"remember": "on"})
        request = RequestFactory().get("/")
        request.user = get_user(self.client)
        request.session = self.client.session
        self.assertFalse(get_observer(request).is_known)

    def test_confirmed_attempt_stands_in_only_for_a_while(self):
        other = self.client_class()
        other.post(self.path, {"remember": "on"})
        SignInAttempt.objects.filter(pk=self.attempt.pk).update(
            confirmed_at=timezone.now() - timedelta(hours=25)
        )
        request = RequestFactory().get("/")
        request.user = get_user(self.client)
        request.session = self.client.session
        self.assertFalse(get_observer(request).is_known)

    def test_email_failure_still_lets_them_start(self):
        with mock.patch(
            "authentication.provisional.send_magic_link",
            side_effect=smtplib.SMTPException("down"),
        ):
            other = self.client_class()
            response = other.post(
                reverse("auth_observe"),
                {"email": "late@example.com", "next": "/"},
                follow=True,
            )
        self.assertContains(response, "couldn&#x27;t send the email")
        self.assertIn(ATTEMPT_SESSION_KEY, other.session)

    def test_resend_sends_a_new_attempt_link(self):
        self.client.post(reverse("auth_observe_resend"), {"next": "/"})
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(
            attempt_in_link(link_path_from_outbox()), self.attempt
        )

    def test_banner_offers_resend(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, "once you open the link")
        self.assertContains(response, reverse("auth_observe_resend"))


class ValidationPromotesObservationsTests(TestCase):
    def test_confirm_moves_waiting_observations_to_moderation(self):
        user = MobilitoUser.objects.create_user("wait@example.com")
        session = make_session(user=user)
        request = RequestFactory().get("/")
        request.session = self.client.session
        user.confirm(request)
        session.refresh_from_db()
        self.assertEqual(
            session.publication_state, PublicationState.PENDING_MODERATION
        )


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ProcessSignInAttemptsTests(TestCase):
    def setUp(self):
        self.user = MobilitoUser.objects.create_user("late@example.com")
        self.attempt = SignInAttempt.objects.create(
            email=self.user.email, user=self.user, created_user=True
        )

    def _age(self, hours, reminded_hours_ago=None):
        SignInAttempt.objects.filter(pk=self.attempt.pk).update(
            created_at=timezone.now() - timedelta(hours=hours),
            last_reminded_at=(
                None
                if reminded_hours_ago is None
                else timezone.now() - timedelta(hours=reminded_hours_ago)
            ),
            reminders_sent=0 if reminded_hours_ago is None else 1,
        )
        self.attempt.refresh_from_db()

    def _run(self, *args):
        out = io.StringIO()
        call_command("process_sign_in_attempts", *args, stdout=out)
        return out.getvalue()

    def test_reminds_once_due_with_a_fresh_long_lived_link(self):
        make_session(user=None, sign_in_attempt=self.attempt)
        self._age(hours=25)
        self._run()
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.reminders_sent, 1)
        self.assertIn("Reminder", mail.outbox[0].subject)
        self.assertIn("what you recorded will be deleted", mail.outbox[0].body)
        self.assertIn("haven't confirmed", mail.outbox[0].body)
        self.assertIn(
            "http://localhost:8000/auth/verify/", mail.outbox[0].body
        )
        link = VERIFY_LINK_RE.search(
            mail.outbox[0].body.replace("localhost:8000", "testserver")
        ).group(1)
        self.assertEqual(attempt_in_link(link), self.attempt)

    def test_not_reminded_too_early(self):
        make_session(user=None, sign_in_attempt=self.attempt)
        self._age(hours=2)
        self._run()
        self.assertEqual(len(mail.outbox), 0)

    def test_drops_after_last_reminder(self):
        session = make_session(user=None, sign_in_attempt=self.attempt)
        location_id = session.location_id
        LocationEvidence.objects.create(
            observation=session, timestamp=timezone.now()
        )
        session.events.create(timestamp=timezone.now(), mode=Mode.CAR)
        self._age(hours=24 * 9, reminded_hours_ago=24 * 8)
        self._run()
        self.assertFalse(SignInAttempt.objects.exists())
        self.assertFalse(ModalShareSession.objects.exists())
        self.assertFalse(ModalShareCountEvent.objects.exists())
        self.assertFalse(LocationEvidence.objects.exists())
        self.assertFalse(Location.objects.filter(pk=location_id).exists())
        # Never confirmed, nothing else: the user record goes too.
        self.assertFalse(MobilitoUser.objects.filter(pk=self.user.pk).exists())

    def test_drop_deletes_stored_photos(self):
        observation = InfrastructureObservation.objects.create(
            location=make_location(),
            observer_perspective="ped",
            sign_in_attempt=self.attempt,
        )
        media = observation.media.create(
            file=SimpleUploadedFile("p.jpg", b"x", content_type="image/jpeg")
        )
        storage, name = media.file.storage, media.file.name
        self.assertTrue(storage.exists(name))
        self._age(hours=24 * 9, reminded_hours_ago=24 * 8)
        with self.captureOnCommitCallbacks(execute=True):
            self._run()
        self.assertFalse(storage.exists(name))
        self.assertFalse(InfrastructureObservation.objects.exists())

    def test_drop_keeps_shared_location_and_real_user(self):
        self.user.email_validated = True
        self.user.save()
        session = make_session(user=None, sign_in_attempt=self.attempt)
        kept = ModalShareSession.objects.create(
            location=session.location,
            started_at=timezone.now(),
            user=self.user,
        )
        self._age(hours=24 * 9, reminded_hours_ago=24 * 8)
        self._run()
        self.assertTrue(Location.objects.filter(pk=kept.location_id).exists())
        self.assertTrue(MobilitoUser.objects.filter(pk=self.user.pk).exists())
        self.assertTrue(ModalShareSession.objects.filter(pk=kept.pk).exists())

    def test_attempt_with_no_data_is_dropped_without_reminder(self):
        self._age(hours=24 * 8)
        self._run()
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(SignInAttempt.objects.exists())

    def test_confirmed_attempts_are_left_alone(self):
        self.attempt.confirm()
        self._age(hours=24 * 30)
        SignInAttempt.objects.filter(pk=self.attempt.pk).update(
            confirmed_at=timezone.now()
        )
        self._run()
        self.assertTrue(SignInAttempt.objects.exists())

    def test_dry_run_changes_nothing(self):
        make_session(user=None, sign_in_attempt=self.attempt)
        self._age(hours=24 * 9, reminded_hours_ago=24 * 8)
        output = self._run("--dry-run")
        self.assertIn("Would have: reminded 0, dropped 1", output)
        self.assertTrue(SignInAttempt.objects.exists())

    def test_stale_attempt_confirmed_meanwhile_is_not_dropped(self):
        session = make_session(user=None, sign_in_attempt=self.attempt)
        stale = SignInAttempt.objects.get(pk=self.attempt.pk)
        self.attempt.confirm()
        self.assertFalse(drop_attempt(stale))
        self.assertTrue(
            ModalShareSession.objects.filter(pk=session.pk).exists()
        )
        self.assertTrue(MobilitoUser.objects.filter(pk=self.user.pk).exists())

    def test_user_that_existed_before_the_attempt_is_kept(self):
        SignInAttempt.objects.filter(pk=self.attempt.pk).update(
            created_user=False
        )
        self._age(hours=24 * 8)
        self._run()
        self.assertTrue(MobilitoUser.objects.filter(pk=self.user.pk).exists())

    def test_user_with_another_attempt_is_kept(self):
        SignInAttempt.objects.create(email=self.user.email, user=self.user)
        self._age(hours=24 * 8)
        self._run()
        self.assertTrue(MobilitoUser.objects.filter(pk=self.user.pk).exists())

    def test_drop_removes_moderation_flags_and_actions(self):
        observation = InfrastructureObservation.objects.create(
            location=make_location(),
            observer_perspective="ped",
            sign_in_attempt=self.attempt,
        )
        ModerationFlag.objects.create(target=observation, reason="other")
        ObservationAction.objects.create(
            observation=observation, action_type="me_too"
        )
        self._age(hours=24 * 9)
        self._run()
        self.assertFalse(ModerationFlag.objects.exists())
        self.assertFalse(ObservationAction.objects.exists())

    def test_catch_up_after_downtime_sends_one_reminder(self):
        make_session(user=None, sign_in_attempt=self.attempt)
        with override_settings(SIGN_IN_ATTEMPT_REMINDER_DELAYS_HOURS=[1, 2]):
            self._age(hours=3)
            self._run()
        self.assertEqual(len(mail.outbox), 1)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.reminders_sent, 2)

    def test_failed_reminder_is_retried_but_drop_still_happens(self):
        make_session(user=None, sign_in_attempt=self.attempt)
        self._age(hours=25)
        with mock.patch(
            "authentication.management.commands.process_sign_in_attempts"
            ".send_magic_link",
            side_effect=smtplib.SMTPRecipientsRefused({}),
        ):
            output = self._run()
            self.assertIn("failed 1", output)
            self._age(hours=24 * 9)
            self._run()
        self.assertFalse(SignInAttempt.objects.exists())

    def test_no_reminder_for_deactivated_user(self):
        make_session(user=None, sign_in_attempt=self.attempt)
        self.user.is_active = False
        self.user.save()
        self._age(hours=25)
        self._run()
        self.assertEqual(len(mail.outbox), 0)

    def test_attempts_cannot_be_deleted_from_admin(self):
        from authentication.admin import SignInAttemptAdmin

        admin = SignInAttemptAdmin(SignInAttempt, None)
        self.assertFalse(admin.has_delete_permission(None))

    def test_deleting_user_with_attempt_data_is_refused(self):
        make_session(user=None, sign_in_attempt=self.attempt)
        with self.assertRaises(ProtectedError):
            self.user.delete()

    def test_link_lifetime_check(self):
        from core.checks import sign_in_attempt_link_check

        self.assertEqual(sign_in_attempt_link_check(None), [])
        with override_settings(SIGN_IN_ATTEMPT_LINK_MAX_AGE=60):
            self.assertEqual(
                sign_in_attempt_link_check(None)[0].id, "core.E001"
            )

    def test_mistyped_address_user_goes_after_the_change(self):
        client = self.client_class()
        client.post(reverse("auth_observe"), {"email": "typo@example.com"})
        typo = SignInAttempt.objects.get(email="typo@example.com")
        make_session(user=None, sign_in_attempt=typo)
        client.post(
            reverse("auth_observe"),
            {"email": "real@example.com", "change": "1"},
        )
        self.assertFalse(
            MobilitoUser.objects.filter(email="typo@example.com").exists()
        )
        # The records moved to the corrected attempt.
        real = SignInAttempt.objects.get(email="real@example.com")
        self.assertEqual(ModalShareSession.objects.get().sign_in_attempt, real)

    def test_same_address_twice_user_goes_after_both_drops(self):
        second = SignInAttempt.objects.create(
            email=self.user.email, user=self.user
        )
        self._age(hours=24 * 8)
        SignInAttempt.objects.filter(pk=second.pk).update(
            created_at=timezone.now() - timedelta(hours=24 * 8)
        )
        self._run()
        self.assertFalse(SignInAttempt.objects.exists())
        self.assertFalse(MobilitoUser.objects.filter(pk=self.user.pk).exists())

    def test_change_after_confirmed_elsewhere_leaves_records(self):
        session = self.client.session
        session[ATTEMPT_SESSION_KEY] = self.attempt.pk
        session.save()
        recorded = make_session(user=None, sign_in_attempt=self.attempt)
        self.attempt.confirm()
        self.client.post(
            reverse("auth_observe"),
            {"email": "other@example.com", "change": "1"},
        )
        recorded.refresh_from_db()
        self.assertEqual(recorded.sign_in_attempt_id, self.attempt.pk)
        self.assertEqual(recorded.user, self.user)
        self.assertTrue(
            SignInAttempt.objects.filter(pk=self.attempt.pk).exists()
        )

    def test_drop_detaches_user_owned_records_instead_of_failing(self):
        owned = make_session(user=self.user, sign_in_attempt=self.attempt)
        self._age(hours=24 * 9)
        output = self._run()
        self.assertIn("dropped 1", output)
        owned.refresh_from_db()
        self.assertIsNone(owned.sign_in_attempt)

    def test_second_confirmation_is_reported_as_such(self):
        self.assertEqual(self.attempt.confirm(), SignInAttempt.CONFIRMED)
        stale = SignInAttempt.objects.get(pk=self.attempt.pk)
        stale.confirmed_at = None
        self.assertEqual(stale.confirm(), SignInAttempt.ALREADY_CONFIRMED)

    def test_reminder_uses_the_language_asked_in(self):
        SignInAttempt.objects.filter(pk=self.attempt.pk).update(language="en")
        make_session(user=None, sign_in_attempt=self.attempt)
        self._age(hours=25)
        with translation.override("fr"):
            self._run()
        self.assertIn('lang="en"', mail.outbox[0].alternatives[0][0])

    def test_dropped_attempt_is_forgotten_by_its_session(self):
        session = self.client.session
        session[ATTEMPT_SESSION_KEY] = self.attempt.pk
        session.save()
        self._age(hours=24 * 8)
        self._run()
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, "once you open the link")
        self.assertContains(response, "anything you recorded was deleted")
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, "wasn&#x27;t confirmed in time")


class SignInAttemptLockOrderTests(TransactionTestCase):
    """Confirming and dropping attempts of one user mustn't deadlock.

    Both take the user row before any attempt row
    (lock_user_first); Postgres would otherwise abort one of them.
    """

    def test_confirm_and_drop_concurrently(self):
        user = MobilitoUser.objects.create_user("lock@example.com")
        old = SignInAttempt.objects.create(
            email=user.email, user=user, created_user=True
        )
        new = SignInAttempt.objects.create(email=user.email, user=user)
        confirmed = threading.Event()
        errors = []

        def confirm_new():
            try:
                with transaction.atomic():
                    new.confirm()
                    confirmed.set()
                    time.sleep(0.5)
                    fresh = MobilitoUser.objects.get(pk=user.pk)
                    fresh.email_validated = True
                    fresh.save()
            except Exception as err:  # noqa: BLE001 (reported below)
                errors.append(err)
            finally:
                connection.close()

        def drop_old():
            confirmed.wait()
            try:
                drop_attempt(old)
            except Exception as err:  # noqa: BLE001
                errors.append(err)
            finally:
                connection.close()

        threads = [
            threading.Thread(target=confirm_new),
            threading.Thread(target=drop_old),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertFalse(SignInAttempt.objects.filter(pk=old.pk).exists())
        user.refresh_from_db()
        self.assertTrue(user.email_validated)
