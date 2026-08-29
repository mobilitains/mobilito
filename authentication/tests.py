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

from django.test import TestCase

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
