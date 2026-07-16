from __future__ import annotations

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from meppp.operations.roles import ROLE_PERMISSIONS


class Command(BaseCommand):
    help = "Create or reconcile the minimal MEPPP operator and moderator groups."

    @transaction.atomic
    def handle(self, *args, **options):
        resolved_permissions: dict[str, list[Permission]] = {}
        missing: list[str] = []

        for role_name, permission_specs in ROLE_PERMISSIONS.items():
            role_permissions: list[Permission] = []
            for app_label, codename in sorted(permission_specs):
                try:
                    permission = Permission.objects.select_related("content_type").get(
                        content_type__app_label=app_label,
                        codename=codename,
                    )
                except Permission.DoesNotExist:
                    missing.append(f"{app_label}.{codename}")
                else:
                    role_permissions.append(permission)
            resolved_permissions[role_name] = role_permissions

        if missing:
            raise CommandError(
                "Required permissions are missing; run migrations first: "
                + ", ".join(sorted(missing))
            )

        results: list[str] = []
        for role_name, permissions in resolved_permissions.items():
            group, created = Group.objects.get_or_create(name=role_name)
            current_ids = set(group.permissions.values_list("pk", flat=True))
            expected_ids = {permission.pk for permission in permissions}
            changed = current_ids != expected_ids
            if changed:
                group.permissions.set(permissions)
            if created:
                status = "created"
            elif changed:
                status = "reconciled"
            else:
                status = "unchanged"
            results.append(f"{role_name}={status}:{len(expected_ids)}")

        self.stdout.write(self.style.SUCCESS("MEPPP roles ready: " + ", ".join(results)))
