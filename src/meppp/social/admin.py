from django.contrib import admin

from .models import EntryLike, Follow


@admin.register(Follow)
class FollowAdmin(admin.ModelAdmin):
    list_display = ("follower", "followed", "created_at")
    search_fields = ("follower__username", "followed__username")
    readonly_fields = ("public_id", "created_at", "updated_at")


@admin.register(EntryLike)
class EntryLikeAdmin(admin.ModelAdmin):
    list_display = ("actor", "entry", "created_at")
    search_fields = ("actor__username", "entry__body")
    readonly_fields = ("public_id", "created_at", "updated_at")
