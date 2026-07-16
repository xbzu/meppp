from django.contrib import admin, messages
from django.contrib.admin.utils import unquote
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from meppp.audit.services import record_event

from .admin_forms import InvitationIssueForm, InvitationRevokeForm
from .models import Invitation, Profile, User
from .services import issue_invitation, revoke_invitation


@admin.register(User)
class MepppUserAdmin(UserAdmin):
    readonly_fields = (*UserAdmin.readonly_fields, "public_id", "last_login", "date_joined")
    list_display = ("username", "email", "is_active", "is_staff", "date_joined")
    list_filter = ("is_active", "is_staff", "is_superuser", "groups")
    search_fields = ("username", "email", "profile__display_name")
    fieldsets = (*UserAdmin.fieldsets, ("Public identity", {"fields": ("public_id",)}))

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        previous = User.objects.filter(pk=obj.pk).first() if change else None
        if previous is not None:
            obj._security_membership_before = {
                "groups": sorted(previous.groups.values_list("name", flat=True)),
                "permissions": sorted(
                    previous.user_permissions.values_list("content_type__app_label", "codename")
                ),
            }
        super().save_model(request, obj, form, change)
        Profile.objects.get_or_create(user=obj)
        if previous is None:
            return
        changes = {
            field: {"before": getattr(previous, field), "after": getattr(obj, field)}
            for field in ("is_active", "is_staff", "is_superuser")
            if getattr(previous, field) != getattr(obj, field)
        }
        if changes:
            record_event(
                actor=request.user,
                action="account.access.changed",
                target_type="user",
                target_public_id=obj.public_id,
                metadata={"changes": changes},
            )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        user = form.instance
        before = getattr(user, "_security_membership_before", None)
        if before is None:
            return
        after = {
            "groups": sorted(user.groups.values_list("name", flat=True)),
            "permissions": sorted(
                user.user_permissions.values_list("content_type__app_label", "codename")
            ),
        }
        if before != after:
            record_event(
                actor=request.user,
                action="account.permissions.changed",
                target_type="user",
                target_public_id=user.public_id,
                metadata={"before": before, "after": after},
            )


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "display_name", "updated_at")
    search_fields = ("user__username", "display_name")
    readonly_fields = ("public_id", "avatar", "created_at", "updated_at")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    add_form_template = "admin/accounts/invitation/issue.html"
    change_form_template = "admin/accounts/invitation/change_form.html"
    list_display = (
        "display_hint",
        "status_display",
        "bound_email",
        "issuer",
        "expires_at",
        "claimed_by",
        "created_at",
    )
    list_filter = ("expires_at", "revoked_at", "claimed_at", "created_at")
    search_fields = ("hint", "bound_email", "issuer__username", "claimed_by__username")
    readonly_fields = (
        "public_id",
        "display_hint",
        "status_display",
        "issuer",
        "bound_email",
        "expires_at",
        "revoked_at",
        "claimed_at",
        "claimed_by",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            "邀请",
            {"fields": ("public_id", "display_hint", "status_display", "bound_email")},
        ),
        (
            "生命周期",
            {
                "fields": (
                    "issuer",
                    "expires_at",
                    "revoked_at",
                    "claimed_at",
                    "claimed_by",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )
    actions = ()

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("issuer", "claimed_by")

    @admin.display(description="邀请码提示", ordering="hint")
    def display_hint(self, obj):
        return f"…{obj.hint}"

    @admin.display(description="状态")
    def status_display(self, obj):
        if obj.claimed_at is not None:
            return "已领取"
        if obj.revoked_at is not None:
            return "已撤销"
        if obj.expires_at <= timezone.now():
            return "已过期"
        return "可使用"

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        opts = self.model._meta
        custom_urls = [
            path(
                "<path:object_id>/revoke/",
                self.admin_site.admin_view(self.revoke_view),
                name=f"{opts.app_label}_{opts.model_name}_revoke",
            )
        ]
        return custom_urls + super().get_urls()

    @sensitive_variables()
    def add_view(self, request, form_url="", extra_context=None):
        if not self.has_add_permission(request):
            raise PermissionDenied
        form = InvitationIssueForm(request.POST or None)
        issued_invitation = None
        plaintext_token = None
        if request.method == "POST" and form.is_valid():
            try:
                issued_invitation, plaintext_token = issue_invitation(
                    issuer=request.user,
                    expires_at=form.cleaned_data["expires_at"],
                    bound_email=form.cleaned_data["bound_email"],
                )
            except ValidationError as error:
                form.add_error(None, error)

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "签发注册邀请",
            "form": form,
            "issued_invitation": issued_invitation,
            "plaintext_token": plaintext_token,
            "changelist_url": reverse("admin:accounts_invitation_changelist"),
        }
        response = TemplateResponse(
            request,
            self.add_form_template,
            {**context, **(extra_context or {})},
        )
        response["Cache-Control"] = "private, no-store"
        return response

    def change_view(self, request, object_id, form_url="", extra_context=None):
        if request.method != "GET":
            raise PermissionDenied("Use the invitation workflow to change invitations")
        invitation = self.get_object(request, unquote(object_id))
        revoke_url = None
        if (
            invitation is not None
            and invitation.claimed_at is None
            and invitation.revoked_at is None
            and self.has_change_permission(request, invitation)
        ):
            revoke_url = reverse("admin:accounts_invitation_revoke", args=[invitation.pk])
        extra_context = {
            **(extra_context or {}),
            "invitation_revoke_url": revoke_url,
            "show_save": False,
            "show_save_and_continue": False,
            "show_save_and_add_another": False,
        }
        return super().change_view(request, object_id, form_url, extra_context)

    def revoke_view(self, request, object_id):
        invitation = self.get_object(request, unquote(object_id))
        if invitation is None:
            raise Http404("Invitation not found")
        if not self.has_change_permission(request, invitation):
            raise PermissionDenied

        form = InvitationRevokeForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                revoke_invitation(
                    invitation=invitation,
                    actor=request.user,
                    reason=form.cleaned_data["reason"],
                )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                self.message_user(request, "邀请已经撤销。", messages.SUCCESS)
                return HttpResponseRedirect(
                    reverse("admin:accounts_invitation_change", args=[invitation.pk])
                )

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "original": invitation,
            "title": "撤销注册邀请",
            "form": form,
            "change_url": reverse("admin:accounts_invitation_change", args=[invitation.pk]),
        }
        response = TemplateResponse(
            request,
            "admin/accounts/invitation/revoke.html",
            context,
        )
        response["Cache-Control"] = "private, no-store"
        return response
