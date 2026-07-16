import uuid

from asgiref.sync import sync_to_async
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxLengthValidator
from django.db import DEFAULT_DB_ALIAS, models, transaction
from django.db.models.functions import Lower

from meppp.common.models import PublicModel

from .normalization import normalize_username


class UserQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError("Users must be deactivated, not deleted")

    def _raw_delete(self, using):
        raise ValidationError("Users must be deactivated, not deleted")


class UserManager(DjangoUserManager.from_queryset(UserQuerySet)):
    def get_by_natural_key(self, username):
        normalized = normalize_username(username)
        return self.get(username__iexact=normalized)

    def _create_user(self, username, email, password, **extra_fields):
        database = self._db or DEFAULT_DB_ALIAS
        with transaction.atomic(using=database):
            user = super()._create_user(username, email, password, **extra_fields)
            Profile.objects.using(database).get_or_create(user_id=user.pk)
        return user

    async def _acreate_user(self, username, email, password, **extra_fields):
        return await sync_to_async(self._create_user, thread_sensitive=True)(
            username,
            email,
            password,
            **extra_fields,
        )


class User(AbstractUser):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    email = models.EmailField(blank=True)

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        base_manager_name = "objects"
        verbose_name = "成员"
        verbose_name_plural = "成员"
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                condition=~models.Q(email=""),
                name="accounts_unique_nonblank_email_ci",
            ),
            models.UniqueConstraint(Lower("username"), name="accounts_unique_username_ci"),
        ]

    def save(self, *args, **kwargs):
        self.username = normalize_username(self.username)
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
        verbose_name = "成员资料"
        verbose_name_plural = "成员资料"

    def __str__(self) -> str:
        return self.display_name or self.user.get_username()


class Invitation(PublicModel):
    token_digest = models.CharField(max_length=64, unique=True, editable=False)
    hint = models.CharField(max_length=8, editable=False)
    issuer = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="issued_invitations",
    )
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    claimed_by = models.OneToOneField(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="claimed_invitation",
    )
    bound_email = models.EmailField(blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "注册邀请"
        verbose_name_plural = "注册邀请"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(claimed_at__isnull=True, claimed_by__isnull=True)
                    | models.Q(claimed_at__isnull=False, claimed_by__isnull=False)
                ),
                name="accounts_invitation_claim_fields_match",
            ),
            models.CheckConstraint(
                condition=models.Q(revoked_at__isnull=True) | models.Q(claimed_at__isnull=True),
                name="accounts_invitation_not_claimed_and_revoked",
            ),
        ]
        indexes = [
            models.Index(
                fields=["claimed_at", "revoked_at", "expires_at"],
                name="accounts_invitation_state_idx",
            )
        ]

    def save(self, *args, **kwargs):
        self.bound_email = self.bound_email.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"…{self.hint}"


class RecoveryCredential(models.Model):
    """A high-entropy, one-time account recovery code stored only as a password hash."""

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="recovery_credential",
    )
    token_digest = models.CharField(max_length=128, editable=False)
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "账号恢复凭据"
        verbose_name_plural = "账号恢复凭据"

    def __str__(self) -> str:
        return f"recovery:{self.user_id}"
