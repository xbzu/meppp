from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.utils import timezone

from meppp.configuration.selectors import get_site_configuration
from meppp.moderation.models import Report, ReportStatus
from meppp.publishing.models import Comment, ContentState, Entry


@dataclass(frozen=True)
class OperationsSnapshot:
    pending_entries: int
    pending_comments: int
    open_reports: int
    triaged_reports: int
    active_members: int
    members_joined_today: int
    members_joined_seven_days: int
    registration_mode: str
    registration_mode_label: str
    moderation_mode: str
    moderation_mode_label: str
    image_uploads_enabled: bool
    video_uploads_enabled: bool
    x_references_enabled: bool
    youtube_references_enabled: bool


def _local_day_start(now: datetime) -> datetime:
    local_now = timezone.localtime(now)
    return timezone.make_aware(
        datetime.combine(local_now.date(), time.min),
        timezone.get_current_timezone(),
    )


def get_operations_snapshot(*, now: datetime | None = None) -> OperationsSnapshot:
    now = now or timezone.now()
    today_start = _local_day_start(now)
    seven_days_start = today_start - timedelta(days=6)

    member_counts = get_user_model().objects.aggregate(
        active=Count("pk", filter=Q(is_active=True)),
        joined_today=Count("pk", filter=Q(date_joined__gte=today_start)),
        joined_seven_days=Count("pk", filter=Q(date_joined__gte=seven_days_start)),
    )
    entry_counts = Entry.objects.aggregate(
        pending=Count("pk", filter=Q(state=ContentState.PENDING))
    )
    comment_counts = Comment.objects.aggregate(
        pending=Count("pk", filter=Q(state=ContentState.PENDING))
    )
    report_counts = Report.objects.aggregate(
        open=Count("pk", filter=Q(status=ReportStatus.OPEN)),
        triaged=Count("pk", filter=Q(status=ReportStatus.TRIAGED)),
    )
    configuration = get_site_configuration()

    return OperationsSnapshot(
        pending_entries=entry_counts["pending"],
        pending_comments=comment_counts["pending"],
        open_reports=report_counts["open"],
        triaged_reports=report_counts["triaged"],
        active_members=member_counts["active"],
        members_joined_today=member_counts["joined_today"],
        members_joined_seven_days=member_counts["joined_seven_days"],
        registration_mode=configuration.registration_mode,
        registration_mode_label=configuration.get_registration_mode_display(),
        moderation_mode=configuration.moderation_mode,
        moderation_mode_label=configuration.get_moderation_mode_display(),
        image_uploads_enabled=configuration.max_images_per_post > 0,
        video_uploads_enabled=configuration.video_uploads_enabled,
        x_references_enabled=configuration.x_references_enabled,
        youtube_references_enabled=configuration.youtube_references_enabled,
    )
