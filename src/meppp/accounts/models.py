import uuid

from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxLengthValidator
from django.db import models
from django.db.models.functions import Lower

from meppp.common.models import PublicModel


class UserQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError("Users must be deactivated, not deleted")

    def _raw_delete(self, using):
        raise ValidationError("Users must be deactivated, not deleted")


class UserManager(DjangoUserManager.from_queryset(UserQuerySet)):
    pass


class User(AbstractUser):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    email = models.EmailField(blank=True)

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        base_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                condition=~models.Q(email=""),
                name="accounts_unique_nonblank_email_ci",
            ),
            models.UniqueConstraint(Lower("username"), name="accounts_unique_username_ci"),
        ]

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Users must be deactivated, not deleted")


class Profile(PublicModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    display_name = models.CharField(max_length=80, blank=True)
    bio = models.TextField(blank=True, validators=[MaxLengthValidator(500)])
    avatar = models.FileField(
        upload_to="avatars/%Y/%m/",
        blank=True,
        validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"])],
    )

    class Meta:
        ordering = ["user_id"]

    def __str__(self) -> str:
        return self.display_name or self.user.get_username()
