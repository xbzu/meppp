from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from meppp.configuration.models import RegistrationMode, SiteConfiguration

from .models import Profile, User


@transaction.atomic
def register_member(*, username: str, email: str, password: str) -> User:
    configuration, _ = SiteConfiguration.objects.select_for_update().get_or_create(pk=1)
    if configuration.registration_mode != RegistrationMode.OPEN:
        raise ValidationError("站点当前未开放注册")
    user = User.objects.create_user(
        username=username,
        email=email,
        password=password,
    )
    Profile.objects.create(user=user)
    return user
