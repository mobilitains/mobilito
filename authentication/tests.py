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

import re
import smtplib
from unittest import mock

from django.contrib.auth import get_user
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from sesame.utils import get_token

from authentication.models import (
    MobilitoUser,
    get_user_by_email,
    normalise_email,
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
        self.assertNotContains(response, "evil.example")

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

    def test_request_stores_language_if_none_set(self):
        self.client.post(
            reverse("auth_start"),
            {"email": "lang@example.com"},
            HTTP_ACCEPT_LANGUAGE="en",
        )
        user = MobilitoUser.objects.get(email="lang@example.com")
        self.assertEqual(user.preferred_language, "en")

    def test_request_keeps_existing_language_preference(self):
        user = MobilitoUser.objects.create_user("keep@example.com")
        user.preferred_language = "fr"
        user.save()
        self.client.post(
            reverse("auth_start"),
            {"email": "keep@example.com"},
            HTTP_ACCEPT_LANGUAGE="en",
        )
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
