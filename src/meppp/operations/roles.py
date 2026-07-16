from __future__ import annotations

from collections.abc import Mapping

PermissionSpec = tuple[str, str]

ROLE_PERMISSIONS: Mapping[str, frozenset[PermissionSpec]] = {
    "运营": frozenset(
        {
            ("configuration", "view_siteconfiguration"),
            ("configuration", "change_siteconfiguration"),
            ("configuration", "view_configurationrevision"),
            ("publishing", "view_entry"),
            ("publishing", "change_entry"),
            ("publishing", "view_comment"),
            ("publishing", "change_comment"),
            ("publishing", "view_topic"),
            ("publishing", "add_topic"),
            ("publishing", "change_topic"),
            ("publishing", "view_videoasset"),
            ("external", "view_externalreference"),
        }
    ),
    "审核": frozenset(
        {
            ("publishing", "view_entry"),
            ("publishing", "change_entry"),
            ("publishing", "view_comment"),
            ("publishing", "change_comment"),
            ("moderation", "view_report"),
            ("moderation", "change_report"),
            ("moderation", "view_moderationdecision"),
            ("publishing", "view_videoasset"),
            ("external", "view_externalreference"),
        }
    ),
}

OPERATIONS_ACCESS_PERMISSIONS = frozenset(
    {
        "configuration.view_siteconfiguration",
        "configuration.change_siteconfiguration",
        "publishing.view_entry",
        "publishing.change_entry",
        "publishing.view_comment",
        "publishing.change_comment",
        "moderation.view_report",
        "moderation.change_report",
    }
)


def has_operations_access(user) -> bool:
    if not user or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser:
        return True
    return bool(OPERATIONS_ACCESS_PERMISSIONS.intersection(user.get_all_permissions()))
