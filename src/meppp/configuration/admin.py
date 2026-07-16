from django import forms
from django.contrib import admin
from django.utils import formats, timezone

from meppp.audit.services import record_event

from .models import ConfigurationRevision, SiteConfiguration
from .services import EDITABLE_FIELDS, update_configuration


class SiteConfigurationAdminForm(forms.ModelForm):
    class Meta:
        model = SiteConfiguration
        fields = (
            "site_name",
            "tagline",
            "registration_mode",
            "post_max_length",
            "comment_max_length",
            "max_images_per_post",
            "upload_max_bytes",
            "moderation_mode",
            "comments_enabled",
            "video_uploads_enabled",
            "x_references_enabled",
            "youtube_references_enabled",
        )
        labels = {
            "site_name": "站点名称",
            "tagline": "站点说明",
            "registration_mode": "注册方式",
            "post_max_length": "正文最大长度",
            "comment_max_length": "评论最大长度",
            "max_images_per_post": "每条内容最多图片数",
            "upload_max_bytes": "单张图片上限（字节）",
            "moderation_mode": "审核方式",
            "comments_enabled": "开放评论",
            "video_uploads_enabled": "允许上传视频",
            "x_references_enabled": "允许分享 X 来源",
            "youtube_references_enabled": "允许分享 YouTube 来源",
        }
        help_texts = {
            "max_images_per_post": "可在 0 到 4 张之间调整；设为 0 会关闭内容配图。",
            "upload_max_bytes": "单张原图和安全处理后的 WebP 都必须小于此值，硬上限为 5 MB。",
            "video_uploads_enabled": "只影响新视频上传；关闭后，已有视频仍可正常播放。",
            "x_references_enabled": "只影响新来源分享；关闭后，已有 X 来源卡片仍会保留。",
            "youtube_references_enabled": (
                "只影响新来源分享；关闭后，已有 YouTube 来源卡片仍会保留。"
            ),
        }


@admin.register(SiteConfiguration)
class SiteConfigurationAdmin(admin.ModelAdmin):
    form = SiteConfigurationAdminForm
    readonly_fields = ("version_display", "created_at_display", "updated_at_display")
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
        (
            "审核与互动",
            {"fields": ("moderation_mode", "comments_enabled")},
        ),
        (
            "发布能力",
            {
                "description": "这些开关只控制新内容，关闭后不会删除已有内容。",
                "fields": (
                    "video_uploads_enabled",
                    "x_references_enabled",
                    "youtube_references_enabled",
                ),
            },
        ),
        (
            "版本",
            {"fields": ("version_display", "created_at_display", "updated_at_display")},
        ),
    )

    @admin.display(description="版本")
    def version_display(self, obj):
        return obj.version if obj is not None else 1

    @admin.display(description="创建时间")
    def created_at_display(self, obj):
        if obj is None or not obj.created_at:
            return "保存后生成"
        return formats.localize(timezone.localtime(obj.created_at))

    @admin.display(description="更新时间")
    def updated_at_display(self, obj):
        if obj is None or not obj.updated_at:
            return "保存后生成"
        return formats.localize(timezone.localtime(obj.updated_at))

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
