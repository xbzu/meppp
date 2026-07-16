from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from meppp.audit.services import record_event

from .models import Profile


@transaction.atomic
def update_member_profile(*, member, display_name: str, bio: str) -> Profile:
    if not member or not member.is_authenticated or not member.is_active:
        raise ValidationError("需要有效的成员账号")

    profile, _ = Profile.objects.select_for_update().get_or_create(user=member)
    before = {"display_name": profile.display_name, "bio": profile.bio}
    profile.display_name = display_name.strip()
    profile.bio = bio.strip()
    profile.full_clean()
    after = {"display_name": profile.display_name, "bio": profile.bio}
    if before == after:
        return profile

    profile.save(update_fields=("display_name", "bio", "updated_at"))
    record_event(
        actor=member,
        action="account.profile.updated",
        target_type="user",
        target_public_id=member.public_id,
        metadata={
            "schema_version": 1,
            "changed_fields": [field for field in before if before[field] != after[field]],
        },
    )
    return profile
