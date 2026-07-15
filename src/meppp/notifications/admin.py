from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "kind", "actor", "read_at", "created_at")
    list_filter = ("kind", "read_at", "created_at")
    search_fields = ("recipient__username", "actor__username")
    readonly_fields = ("public_id", "created_at", "updated_at")
