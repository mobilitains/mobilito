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

# Moderation for v1-preview happens here (roadmap Phase 8, design
# §13): filter observations by state, open ones, publish / hold /
# sandbox them (bulk actions on the list, or buttons on an
# observation's own page), and review flags. The publication state
# is never edited as a field: only those change it, through
# core.lifecycle.moderate, which refuses to show others anything
# whose author hasn't confirmed their email.
#
# Observations and photos can't be deleted here: moderation keeps
# content (§13.1); sandbox it instead.

from django.contrib import admin, messages
from django.contrib.contenttypes.admin import GenericTabularInline
from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, F, OuterRef
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from modeltranslation.admin import TranslationAdmin

from core.lifecycle import TransitionError, moderate
from core.models import ModerationState, PublicationState
from mobilito_app.models import (
    ContactMethod,
    InfrastructureMedia,
    InfrastructureObservation,
    InfrastructureTag,
    ModalShareSession,
    ModerationFlag,
)
from mobilito_app.moderation import resolve_flags

# (target state, action name, menu label, button label, past tense)
OUTCOMES = [
    (
        PublicationState.PUBLISHED,
        "publish",
        _("Publish (show to everyone)"),
        _("Publish"),
        _("Published"),
    ),
    (
        PublicationState.LIGHT_HOLD,
        "light_hold",
        _("Light hold (link only, off the map)"),
        _("Light hold"),
        _("Put on light hold"),
    ),
    (
        PublicationState.SANDBOXED,
        "sandbox",
        _("Sandbox (author and moderators only)"),
        _("Sandbox"),
        _("Sandboxed"),
    ),
]


def _open_flags(model):
    """Subquery: this observation has flags nobody has dealt with."""
    return Exists(
        ModerationFlag.objects.filter(
            content_type=ContentType.objects.get_for_model(model),
            object_id=OuterRef("pk"),
            resolved_at__isnull=True,
        )
    )


def _open_photo_flags():
    return Exists(
        ModerationFlag.objects.filter(
            content_type=ContentType.objects.get_for_model(
                InfrastructureMedia
            ),
            object_id__in=InfrastructureMedia.objects.filter(
                observation=OuterRef(OuterRef("pk"))
            ).values("pk"),
            resolved_at__isnull=True,
        )
    )


class OpenFlagsFilter(admin.SimpleListFilter):
    title = _("open flags")
    parameter_name = "flagged"

    def lookups(self, request, model_admin):
        return [("yes", _("Has open flags")), ("no", _("No open flags"))]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(has_open_flags=True)
        if self.value() == "no":
            return queryset.filter(has_open_flags=False)
        return queryset


class FlagStatusFilter(admin.SimpleListFilter):
    title = _("status")
    parameter_name = "status"

    def lookups(self, request, model_admin):
        return [("open", _("Still open")), ("done", _("Dealt with"))]

    def queryset(self, request, queryset):
        if self.value() == "open":
            return queryset.filter(resolved_at__isnull=True)
        if self.value() == "done":
            return queryset.filter(resolved_at__isnull=False)
        return queryset


class FlagInline(GenericTabularInline):
    model = ModerationFlag
    ct_field = "content_type"
    ct_fk_field = "object_id"
    extra = 0
    can_delete = False
    fields = ("reason", "note", "reporter", "created_at", "resolved_at")
    readonly_fields = fields
    verbose_name_plural = _("flags")

    def has_add_permission(self, request, obj=None):
        return False


def _make_action(target, name, label, verb):
    def action(modeladmin, request, queryset):
        modeladmin.moderate_all(request, queryset, target, verb)

    action.__name__ = name
    return admin.action(description=label, permissions=["change"])(action)


class ObservationAdmin(admin.ModelAdmin):
    """Shared moderation for counts and reports."""

    change_form_template = "admin/mobilito_app/observation_change_form.html"
    actions = [
        _make_action(target, name, label, verb)
        for target, name, label, _button, verb in OUTCOMES
    ]
    list_filter = ("publication_state", OpenFlagsFilter)
    date_hierarchy = "created_at"
    search_fields = (
        "location__user_entered_address",
        "location__reverse_geocoded_address",
        "location__commune",
        "user__email",
    )
    list_select_related = ("location", "user")
    inlines = [FlagInline]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(has_open_flags=self.open_flags_expression())
        )

    def open_flags_expression(self):
        return _open_flags(self.model)

    @admin.display(boolean=True, description=_("open flags"))
    def open_flags(self, obj):
        return obj.has_open_flags

    @admin.display(boolean=True, description=_("email confirmed"))
    def author_confirmed(self, obj):
        return bool(obj.user and obj.user.email_validated)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def moderate_one(self, request, observation, target, verb):
        """Returns (changed, refusal reason or None)."""
        try:
            changed = moderate(observation, target)
        except TransitionError as err:
            return False, str(err)
        resolved = 0
        if target == PublicationState.PUBLISHED:
            # Looked at and found fine: its flags are dealt with, and
            # mustn't hide it again at the next one.
            resolved = self.resolve_all_flags(observation, request.user)
        elif target == PublicationState.SANDBOXED:
            # A final decision too: everything in it is dealt with.
            # (Light hold means "still being checked": flags stay.)
            resolved = self.resolve_all_flags(
                observation, request.user, hidden_photos=True
            )
        if changed or resolved:
            message = str(verb)
            if resolved:
                message += " " + gettext("(%(n)d flag(s) dealt with)") % {
                    "n": resolved
                }
            self.log_change(request, observation, message)
        return changed, None

    def moderate_all(self, request, queryset, target, verb):
        moved, unchanged, refused = 0, 0, []
        for observation in queryset.select_related("user"):
            changed, reason = self.moderate_one(
                request, observation, target, verb
            )
            if reason:
                refused.append(f"#{observation.pk}: {reason}")
            elif changed:
                moved += 1
            else:
                unchanged += 1
        if moved:
            self.message_user(
                request,
                gettext("%(verb)s: %(n)d.") % {"verb": verb, "n": moved},
                messages.SUCCESS,
            )
        if unchanged:
            self.message_user(
                request,
                gettext("Already in that state: %(n)d.") % {"n": unchanged},
                messages.INFO,
            )
        if refused:
            self.message_user(
                request,
                gettext("Not changed: %(list)s")
                % {"list": "; ".join(refused)},
                messages.WARNING,
            )

    def resolve_all_flags(self, observation, user, hidden_photos=False):
        return resolve_flags(observation, user)

    # Anything to save besides the moderation buttons (photos).
    has_editable_parts = False

    def render_change_form(self, request, context, *args, obj=None, **kw):
        can_moderate = obj is not None and self.has_change_permission(
            request, obj
        )
        context["moderation_buttons"] = [
            (name, label)
            for target, name, label, _button, _verb in OUTCOMES
            if can_moderate and obj.publication_state != target
        ]
        context["has_editable_parts"] = self.has_editable_parts
        if not self.has_editable_parts:
            # Nothing else to save: only the decision buttons.
            context["show_save"] = False
            context["show_save_and_continue"] = False
        return super().render_change_form(
            request, context, *args, obj=obj, **kw
        )

    def log_change(self, request, obj, message):
        # A decision button also goes through the normal save, which
        # would log an empty "No fields changed." beside the decision.
        if message:
            super().log_change(request, obj, message)

    def save_model(self, request, obj, form, change):
        # Every field is read-only: saving would only rewrite the row
        # as loaded (and could undo an automatic hold made meanwhile).
        if form.changed_data:
            super().save_model(request, obj, form, change)

    def response_change(self, request, obj):
        for target, name, _label, _button, verb in OUTCOMES:
            if f"_moderate_{name}" in request.POST:
                if not self.has_change_permission(request, obj):
                    break
                obj.refresh_from_db()
                changed, reason = self.moderate_one(request, obj, target, verb)
                if reason:
                    self.message_user(
                        request,
                        gettext("Not changed: %(list)s") % {"list": reason},
                        messages.WARNING,
                    )
                else:
                    self.message_user(request, verb, messages.SUCCESS)
                return HttpResponseRedirect(request.path)
        return super().response_change(request, obj)


def _flat(fields):
    return [
        name
        for item in fields
        for name in (item if isinstance(item, tuple) else (item,))
    ]


@admin.register(ModalShareSession)
class ModalShareSessionAdmin(ObservationAdmin):
    list_display = (
        "pk",
        "location",
        "started_at",
        "publication_state",
        "total",
        "location_mismatch",
        "author_confirmed",
        "open_flags",
    )
    list_filter = ObservationAdmin.list_filter + ("location_mismatch",)
    fields = (
        "location",
        "user",
        "sign_in_attempt",
        "publication_state",
        ("started_at", "finished_at"),
        ("total_pedestrian", "total_cyclist", "total_car", "total_tc"),
        "location_mismatch",
        "integrity_hash",
        ("created_at", "updated_at"),
    )
    readonly_fields = _flat(fields)

    @admin.display(description=_("counted"))
    def total(self, obj):
        return sum(obj.totals().values())


class MediaInline(admin.TabularInline):
    """Photos: untick "published" to hide one from the public."""

    model = InfrastructureMedia
    fk_name = "observation"
    extra = 0
    can_delete = False
    fields = ("preview", "published", "moderation_state", "flags")
    readonly_fields = ("preview", "moderation_state", "flags")
    verbose_name_plural = _(
        "photos (untick “published” to hide one from the public)"
    )

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description=_("photo"))
    def preview(self, obj):
        url = reverse("reports_photo", args=[obj.observation_id, obj.pk])
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">'
            '<img src="{}" alt="" style="max-height: 8rem;"></a>',
            url,
            url,
        )

    @admin.display(description=_("open flags"))
    def flags(self, obj):
        flags = obj.moderation_flags.filter(resolved_at__isnull=True)
        return (
            format_html_join(
                "",
                "<div>{}{}</div>",
                (
                    (f.get_reason_display(), f": {f.note}" if f.note else "")
                    for f in flags
                ),
            )
            or "-"
        )


@admin.register(InfrastructureObservation)
class InfrastructureObservationAdmin(ObservationAdmin):
    list_display = (
        "pk",
        "location",
        "created_at",
        "publication_state",
        "moderation_state",
        "author_confirmed",
        "open_flags",
    )
    list_filter = ObservationAdmin.list_filter + ("moderation_state",)
    search_fields = ObservationAdmin.search_fields + ("description",)
    fields = (
        "location",
        "user",
        "sign_in_attempt",
        "publication_state",
        "moderation_state",
        "observer_perspective",
        "description",
        "tags",
        ("created_at", "updated_at"),
    )
    readonly_fields = _flat(fields)
    inlines = [MediaInline, FlagInline]
    has_editable_parts = True

    def open_flags_expression(self):
        # The report's own text, or any of its photos.
        return _open_flags(self.model) | _open_photo_flags()

    def resolve_all_flags(self, observation, user, hidden_photos=False):
        """The observation's flags, and its photos' (hidden ones too
        if asked: a hidden photo's flags stay open until decided)."""
        resolved = resolve_flags(observation, user)
        photos = observation.media.all()
        if not hidden_photos:
            photos = photos.filter(published=True)
        for item in photos:
            resolved += resolve_flags(item, user)
        return resolved

    def save_formset(self, request, form, formset, change):
        if formset.model is InfrastructureMedia and any(
            key.startswith("_moderate_") for key in request.POST
        ):
            # A decision button isn't a decision about photos: the
            # boxes may be stale (a photo hidden automatically since
            # the page loaded). Only Save applies them. (The history
            # message reads what a save would have set.)
            formset.new_objects = []
            formset.changed_objects = []
            formset.deleted_objects = []
            return
        # A moderator deciding about a photo deals with its flags,
        # whether they show it or keep it hidden; its moderation
        # state follows.
        for photo_form in formset.forms:
            item = photo_form.instance
            if isinstance(item, InfrastructureMedia) and (
                photo_form.has_changed()
            ):
                item.moderation_state = (
                    ModerationState.CLEARED
                    if item.published
                    else ModerationState.FLAGGED
                )
        super().save_formset(request, form, formset, change)
        for photo_form in formset.forms:
            item = photo_form.instance
            if not (
                isinstance(item, InfrastructureMedia)
                and photo_form.has_changed()
            ):
                continue
            resolve_flags(item, request.user)
            self.log_change(
                request,
                item.observation,
                (
                    gettext("Photo %(pk)d: shown to the public")
                    % {"pk": item.pk}
                    if item.published
                    else gettext("Photo %(pk)d: hidden from the public")
                    % {"pk": item.pk}
                ),
            )


@admin.register(ModerationFlag)
class ModerationFlagAdmin(admin.ModelAdmin):
    """Flags from the public (§13.4). Kept as the audit trail."""

    list_display = (
        "created_at",
        "reason",
        "target_link",
        "target_state",
        "reporter",
        "resolved_at",
    )
    list_filter = (FlagStatusFilter, "reason")
    # Still open first, newest first.
    ordering = (F("resolved_at").asc(nulls_first=True), "-created_at")
    list_select_related = ("reporter", "content_type")
    date_hierarchy = "created_at"
    actions = ["mark_resolved"]
    fields = (
        "target_link",
        "target_state",
        "reason",
        "note",
        "reporter",
        "created_at",
        "resolved_at",
        "resolved_by",
    )
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @staticmethod
    def _observation(flag):
        target = flag.target
        if isinstance(target, InfrastructureMedia):
            return target.observation
        return target

    @admin.display(description=_("flagged"))
    def target_link(self, flag):
        target = flag.target
        observation = self._observation(flag)
        if observation is None:
            return gettext("(gone)")
        opts = observation._meta
        url = reverse(
            f"admin:{opts.app_label}_{opts.model_name}_change",
            args=[observation.pk],
        )
        if isinstance(target, InfrastructureMedia):
            label = gettext("photo in %(what)s") % {"what": observation}
        else:
            label = str(observation)
        return format_html('<a href="{}">{}</a>', url, label)

    @admin.display(description=_("state now"))
    def target_state(self, flag):
        target = flag.target
        observation = self._observation(flag)
        if observation is None:
            return "-"
        state = observation.get_publication_state_display()
        if isinstance(target, InfrastructureMedia) and not target.published:
            state += " " + gettext("(photo hidden)")
        return state

    @admin.action(
        description=_("Mark as dealt with (no change to the content)"),
        permissions=["change"],
    )
    def mark_resolved(self, request, queryset):
        count = queryset.filter(resolved_at__isnull=True).update(
            resolved_at=timezone.now(), resolved_by=request.user
        )
        self.message_user(
            request,
            gettext("Marked %(n)d flag(s) as dealt with.") % {"n": count},
        )


@admin.register(InfrastructureTag)
class InfrastructureTagAdmin(TranslationAdmin):
    list_display = ("label", "family", "country", "status")
    list_filter = ("status", "country", "family")
    search_fields = ("label", "description")


@admin.register(ContactMethod)
class ContactMethodAdmin(admin.ModelAdmin):
    list_display = (
        "contact_value",
        "country",
        "department",
        "commune",
        "do_not_contact",
    )
    list_filter = ("do_not_contact", "country")
    search_fields = ("contact_value", "commune")
    raw_id_fields = ("user",)
