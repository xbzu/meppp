from django.contrib import admin

from .models import ExternalReference


@admin.register(ExternalReference)
class ExternalReferenceAdmin(admin.ModelAdmin):
    actions = ()
    list_display = (
        "provider",
        "external_id",
        "entry",
        "metadata_status",
        "fetched_at",
        "expires_at",
    )
    list_filter = ("provider", "metadata_status")
    search_fields = (
        "external_id",
        "canonical_url",
        "author_name",
        "title",
        "entry__body",
    )
    raw_id_fields = ("entry",)
    readonly_fields = tuple(field.name for field in ExternalReference._meta.concrete_fields)

    def has_add_permission(self, request):
        return False

    def has_view_permission(self, request, obj=None):
        return bool(
            request.user.is_active
            and request.user.is_staff
            and request.user.has_perm("external.view_externalreference")
        )

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
