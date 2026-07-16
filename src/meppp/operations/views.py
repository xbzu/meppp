from __future__ import annotations

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.urls import reverse

from .roles import has_operations_access
from .selectors import OperationsSnapshot, get_operations_snapshot


def _has_any_permission(permissions: set[str], *required: str) -> bool:
    return bool(permissions.intersection(required))


def _quick_links(*, user, snapshot: OperationsSnapshot) -> list[dict]:
    if user.is_superuser:
        permissions: set[str] | None = None
    else:
        permissions = user.get_all_permissions()

    def allowed(*required: str) -> bool:
        return permissions is None or _has_any_permission(permissions, *required)

    links: list[dict] = []
    if allowed("publishing.view_entry", "publishing.change_entry"):
        links.append(
            {
                "label": "待审内容",
                "count": snapshot.pending_entries,
                "url": reverse("admin:publishing_pendingentry_changelist"),
            }
        )
    if allowed("publishing.view_comment", "publishing.change_comment"):
        links.append(
            {
                "label": "待审评论",
                "count": snapshot.pending_comments,
                "url": reverse("admin:publishing_pendingcomment_changelist"),
            }
        )
    if allowed("moderation.view_report", "moderation.change_report"):
        links.extend(
            (
                {
                    "label": "待处理举报",
                    "count": snapshot.open_reports,
                    "url": f"{reverse('admin:moderation_report_changelist')}?status__exact=open",
                },
                {
                    "label": "已分派举报",
                    "count": snapshot.triaged_reports,
                    "url": f"{reverse('admin:moderation_report_changelist')}?status__exact=triaged",
                },
            )
        )
    if allowed(
        "configuration.view_siteconfiguration",
        "configuration.change_siteconfiguration",
    ):
        links.append(
            {
                "label": "站点配置",
                "count": None,
                "url": reverse("admin:configuration_siteconfiguration_changelist"),
            }
        )
    if allowed("accounts.view_invitation", "accounts.add_invitation"):
        links.append(
            {
                "label": "注册邀请",
                "count": None,
                "url": reverse("admin:accounts_invitation_changelist"),
            }
        )
    if allowed("publishing.view_topic", "publishing.change_topic"):
        links.append(
            {
                "label": "话题管理",
                "count": None,
                "url": reverse("admin:publishing_topic_changelist"),
            }
        )
    if allowed("accounts.view_user", "accounts.change_user"):
        links.append(
            {
                "label": "可用成员",
                "count": snapshot.active_members,
                "url": f"{reverse('admin:accounts_user_changelist')}?is_active__exact=1",
            }
        )
    if allowed("audit.view_auditevent"):
        links.append(
            {
                "label": "审计记录",
                "count": None,
                "url": reverse("admin:audit_auditevent_changelist"),
            }
        )
    return links


def dashboard(request):
    if not has_operations_access(request.user):
        raise PermissionDenied

    snapshot = get_operations_snapshot()
    context = {
        **admin.site.each_context(request),
        "title": "运营总览",
        "snapshot": snapshot,
        "quick_links": _quick_links(user=request.user, snapshot=snapshot),
    }
    return render(request, "admin/operations/dashboard.html", context)
