from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "target_type", "actor", "request_id")
    list_filter = ("action", "target_type", "created_at")
    search_fields = ("reason", "actor__username", "request_id")
    readonly_fields = (
        "public_id",
        "actor",
        "action",
        "target_type",
        "target_public_id",
        "request_id",
        "reason",
        "metadata",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
