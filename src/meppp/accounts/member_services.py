from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction

from meppp.audit.services import record_event
from meppp.configuration.models import MAX_IMAGE_UPLOAD_BYTES
from meppp.configuration.selectors import get_site_configuration
from meppp.publishing.image_processing import ProcessedImage, process_image_upload
from meppp.publishing.services import (
    cleanup_stored_files,
    preflight_member_media_capacity,
)

from .models import Profile

AVATAR_OUTPUT_SIZE = 512
MAX_AVATAR_OUTPUT_BYTES = 1024 * 1024


def process_member_avatar(*, upload) -> ProcessedImage:
    configuration = get_site_configuration()
    if not configuration.avatar_uploads_enabled:
        raise ValidationError("管理员当前关闭了新头像上传")
    return process_image_upload(
        upload=upload,
        max_bytes=min(configuration.upload_max_bytes, MAX_IMAGE_UPLOAD_BYTES),
        square_size=AVATAR_OUTPUT_SIZE,
        max_output_bytes=MAX_AVATAR_OUTPUT_BYTES,
    )


def _validate_avatar(*, avatar: ProcessedImage, maximum_input_bytes: int) -> None:
    if not isinstance(avatar, ProcessedImage):
        raise ValidationError("头像尚未完成安全处理")
    if (
        avatar.mime_type != "image/webp"
        or avatar.source_byte_size <= 0
        or avatar.source_byte_size > maximum_input_bytes
        or avatar.byte_size <= 0
        or avatar.byte_size > MAX_AVATAR_OUTPUT_BYTES
        or avatar.byte_size != len(avatar.content)
        or avatar.width != AVATAR_OUTPUT_SIZE
        or avatar.height != AVATAR_OUTPUT_SIZE
    ):
        raise ValidationError("头像处理结果无效")


def update_member_profile(
    *,
    member,
    display_name: str,
    bio: str,
    avatar: ProcessedImage | None = None,
    remove_avatar: bool = False,
) -> Profile:
    if not member or not member.is_authenticated or not member.is_active:
        raise ValidationError("需要有效的成员账号")
    if avatar is not None and remove_avatar:
        raise ValidationError("上传新头像和删除头像不能同时选择")

    configuration = get_site_configuration()
    maximum_input_bytes = min(configuration.upload_max_bytes, MAX_IMAGE_UPLOAD_BYTES)
    if avatar is not None:
        if not configuration.avatar_uploads_enabled:
            raise ValidationError("管理员当前关闭了新头像上传")
        _validate_avatar(avatar=avatar, maximum_input_bytes=maximum_input_bytes)
        preflight_member_media_capacity(
            author=member,
            expected_media_bytes=avatar.byte_size,
        )

    stored_files: list[tuple] = []
    try:
        with transaction.atomic():
            profile, _ = Profile.objects.select_for_update().get_or_create(user=member)
            before = {
                "display_name": profile.display_name,
                "bio": profile.bio,
                "avatar": bool(profile.avatar),
            }
            profile.display_name = display_name.strip()
            profile.bio = bio.strip()

            if avatar is not None:
                preflight_member_media_capacity(
                    author=member,
                    expected_media_bytes=avatar.byte_size,
                )
                revision = uuid.uuid4()
                expected_name = f"avatars/{profile.public_id}/{revision}.webp"
                stored_name = profile.avatar.storage.save(
                    expected_name,
                    ContentFile(avatar.content),
                )
                stored_files.append((profile.avatar.storage, stored_name))
                if stored_name != expected_name:
                    raise ValidationError("头像保存路径发生冲突，请重新上传")
                profile.avatar.name = stored_name
                profile.avatar_version = revision
                profile.avatar_byte_size = avatar.byte_size
                profile.avatar_width = avatar.width
                profile.avatar_height = avatar.height
            elif remove_avatar and profile.avatar:
                profile.avatar = ""
                profile.avatar_version = None
                profile.avatar_byte_size = None
                profile.avatar_width = None
                profile.avatar_height = None

            profile.full_clean()
            after = {
                "display_name": profile.display_name,
                "bio": profile.bio,
                "avatar": bool(profile.avatar),
            }
            changed_fields = [field for field in before if before[field] != after[field]]
            if avatar is not None and "avatar" not in changed_fields:
                changed_fields.append("avatar")
            if not changed_fields:
                return profile

            update_fields = ["display_name", "bio", "updated_at"]
            if "avatar" in changed_fields:
                update_fields.extend(
                    (
                        "avatar",
                        "avatar_version",
                        "avatar_byte_size",
                        "avatar_width",
                        "avatar_height",
                    )
                )
            profile.save(update_fields=update_fields)
            record_event(
                actor=member,
                action="account.profile.updated",
                target_type="user",
                target_public_id=member.public_id,
                metadata={
                    "schema_version": 1,
                    "changed_fields": changed_fields,
                },
            )
        return profile
    except BaseException:
        cleanup_stored_files(stored_files)
        raise
