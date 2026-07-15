from django.contrib import admin

from meppp.audit.services import record_event

from .models import ModerationDecision, Report


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ("public_id", "subject_type", "reason", "status", "reporter", "created_at")
    list_filter = ("status", "subject_type", "reason", "created_at")
    search_fields = ("details", "reporter__username", "subject_public_id")
    readonly_fields = (
        "public_id",
        "reporter",
        "subject_type",
        "subject_public_id",
        "reason",
        "details",
        "status",
        "resolved_by",
        "resolved_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        previous_assignee = (
            Report.objects.filter(pk=obj.pk).values_list("assigned_to_id", flat=True).first()
            if change
            else None
        )
        super().save_model(request, obj, form, change)
        if previous_assignee != obj.assigned_to_id:
            record_event(
                actor=request.user,
                action="moderation.report.assigned",
                target_type="report",
                target_public_id=obj.public_id,
                metadata={
                    "before_user_id": previous_assignee,
                    "after_user_id": obj.assigned_to_id,
                },
            )


@admin.register(ModerationDecision)
class ModerationDecisionAdmin(admin.ModelAdmin):
    list_display = ("report", "action", "actor", "created_at")
    readonly_fields = (
        "public_id",
        "report",
        "actor",
        "action",
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
