from django.contrib import admin

from meppp.audit.services import record_event

from .models import Comment, Entry, EntryTopic, Topic


@admin.register(Entry)
class EntryAdmin(admin.ModelAdmin):
    list_display = ("public_id", "author", "state", "created_at", "updated_at")
    list_filter = ("state", "created_at")
    search_fields = ("body", "author__username")
    readonly_fields = ("public_id", "author", "body", "edited_at", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        previous_state = Entry.objects.filter(pk=obj.pk).values_list("state", flat=True).first()
        super().save_model(request, obj, form, change)
        if previous_state is not None and previous_state != obj.state:
            record_event(
                actor=request.user,
                action="entry.state.changed",
                target_type="entry",
                target_public_id=obj.public_id,
                metadata={"before": previous_state, "after": obj.state},
            )


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("public_id", "entry", "author", "state", "created_at")
    list_filter = ("state", "created_at")
    search_fields = ("body", "author__username")
    readonly_fields = (
        "public_id",
        "entry",
        "author",
        "body",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        previous_state = Comment.objects.filter(pk=obj.pk).values_list("state", flat=True).first()
        super().save_model(request, obj, form, change)
        if previous_state is not None and previous_state != obj.state:
            record_event(
                actor=request.user,
                action="comment.state.changed",
                target_type="comment",
                target_public_id=obj.public_id,
                metadata={"before": previous_state, "after": obj.state},
            )


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ("slug", "label", "created_at")
    search_fields = ("slug", "label")
    readonly_fields = ("public_id", "created_at", "updated_at")


admin.site.register(EntryTopic)
