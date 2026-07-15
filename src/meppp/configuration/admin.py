from django.contrib import admin

from meppp.audit.services import record_event

from .models import ConfigurationRevision, SiteConfiguration
from .services import EDITABLE_FIELDS, update_configuration


@admin.register(SiteConfiguration)
class SiteConfigurationAdmin(admin.ModelAdmin):
    readonly_fields = ("version", "created_at", "updated_at")
    fieldsets = (
        ("站点", {"fields": ("site_name", "tagline", "registration_mode")}),
        (
            "内容限制",
            {
                "fields": (
                    "post_max_length",
                    "comment_max_length",
                    "max_images_per_post",
                    "upload_max_bytes",
                )
            },
        ),
        ("审核与功能", {"fields": ("moderation_mode", "comments_enabled")}),
        ("版本", {"fields": ("version", "created_at", "updated_at")}),
    )

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not SiteConfiguration.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        changes = {
            field_name: form.cleaned_data[field_name]
            for field_name in EDITABLE_FIELDS
            if field_name in form.cleaned_data
        }
        if change:
            saved = update_configuration(
                actor=request.user,
                changes=changes,
                reason="Updated through Django Admin",
            )
            obj.__dict__.update(saved.__dict__)
            return

        super().save_model(request, obj, form, change)
        ConfigurationRevision.objects.create(
            version=obj.version,
            snapshot=obj.snapshot(),
            actor=request.user,
            reason="Initial configuration created through Django Admin",
        )
        record_event(
            actor=request.user,
            action="configuration.created",
            target_type="site_configuration",
            metadata={"after": obj.snapshot(), "version": obj.version},
        )


@admin.register(ConfigurationRevision)
class ConfigurationRevisionAdmin(admin.ModelAdmin):
    list_display = ("version", "actor", "created_at", "reason")
    readonly_fields = (
        "public_id",
        "version",
        "snapshot",
        "actor",
        "reason",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
