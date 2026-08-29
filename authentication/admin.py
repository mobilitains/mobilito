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

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import MobilitoUser


@admin.register(MobilitoUser)
class MobilitoUserAdmin(UserAdmin):
    list_display = (
        "email",
        "is_active",
        "is_staff",
        "email_validated",
        "created_at",
    )
    list_filter = ("is_active", "is_staff", "email_validated")
    search_fields = ("email",)
    ordering = ("email",)
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Status",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "email_validated",
                    "preferred_language",
                    "use_device_location",
                )
            },
        ),
        (
            "Permissions",
            {"fields": ("groups", "user_permissions")},
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at")},
        ),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2"),
            },
        ),
    )
