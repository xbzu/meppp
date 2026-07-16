from __future__ import annotations

from django.db.models import BooleanField, Count, Exists, OuterRef, Q, Value

from meppp.social.models import EntryLike

from .models import Comment, ContentState, Entry, Topic


def public_entries(*, viewer=None):
    queryset = (
        Entry.objects.public()
        .filter(author__is_active=True)
        .select_related("author", "author__profile", "video", "external_reference")
        .prefetch_related("topics", "attachments")
        .annotate(
            like_count=Count("likes", distinct=True),
            comment_count=Count(
                "comments",
                filter=Q(
                    comments__state=ContentState.PUBLISHED,
                    comments__author__is_active=True,
                ),
                distinct=True,
            ),
        )
        .order_by("-created_at", "-pk")
    )
    if viewer is not None and viewer.is_authenticated:
        return queryset.annotate(
            viewer_liked=Exists(EntryLike.objects.filter(actor=viewer, entry_id=OuterRef("pk")))
        )
    return queryset.annotate(viewer_liked=Value(False, output_field=BooleanField()))


def public_comments(*, entry: Entry):
    return (
        Comment.objects.filter(
            entry=entry,
            state=ContentState.PUBLISHED,
            author__is_active=True,
        )
        .select_related("author", "author__profile")
        .order_by("created_at", "pk")
    )


def active_topics(*, limit: int = 8):
    return (
        Topic.objects.annotate(
            entry_count=Count(
                "entries",
                filter=Q(
                    entries__state=ContentState.PUBLISHED,
                    entries__author__is_active=True,
                ),
                distinct=True,
            )
        )
        .filter(entry_count__gt=0)
        .order_by("-entry_count", "label")[:limit]
    )
