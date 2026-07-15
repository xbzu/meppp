from django.conf import settings
from django.db import models

from meppp.common.models import PublicModel
from meppp.publishing.models import Entry


class Follow(PublicModel):
    follower = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="following_links",
    )
    followed = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="follower_links",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["follower", "followed"], name="social_unique_follow"),
            models.CheckConstraint(
                condition=~models.Q(follower=models.F("followed")),
                name="social_prevent_self_follow",
            ),
        ]
        indexes = [
            models.Index(fields=["follower", "-created_at"]),
            models.Index(fields=["followed", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.follower_id} → {self.followed_id}"


class EntryLike(PublicModel):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="entry_likes",
    )
    entry = models.ForeignKey(Entry, on_delete=models.CASCADE, related_name="likes")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["actor", "entry"], name="social_unique_entry_like")
        ]
        indexes = [models.Index(fields=["entry", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.actor_id}:{self.entry_id}"
