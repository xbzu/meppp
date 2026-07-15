from __future__ import annotations

from meppp.configuration.selectors import get_site_configuration
from meppp.notifications.models import Notification


def site_context(request):
    unread_count = 0
    if request.user.is_authenticated:
        unread_count = Notification.objects.filter(
            recipient=request.user,
            read_at__isnull=True,
        ).count()
    return {
        "site_config": get_site_configuration(),
        "unread_notification_count": min(unread_count, 99),
    }
