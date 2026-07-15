from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from meppp.audit.services import record_event

from .models import Profile, User


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
