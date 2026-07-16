import uuid

import django.db.models.deletion
from django.db import migrations, models


def create_missing_profiles(apps, schema_editor):
    user_model = apps.get_model("accounts", "User")
    profile_model = apps.get_model("accounts", "Profile")
    database = schema_editor.connection.alias
    profiled_user_ids = profile_model.objects.using(database).values_list("user_id", flat=True)
    missing_user_ids = (
        user_model.objects.using(database)
        .exclude(pk__in=profiled_user_ids)
        .values_list("pk", flat=True)
    )
    for user_id in missing_user_ids.iterator():
        profile_model.objects.using(database).create(user_id=user_id)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_alter_profile_options_alter_user_options"),
    ]

    operations = [
        migrations.CreateModel(
            name="Invitation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "public_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("token_digest", models.CharField(editable=False, max_length=64, unique=True)),
                ("hint", models.CharField(editable=False, max_length=8)),
                ("expires_at", models.DateTimeField()),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("claimed_at", models.DateTimeField(blank=True, null=True)),
                ("bound_email", models.EmailField(blank=True, max_length=254)),
                (
                    "claimed_by",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="claimed_invitation",
                        to="accounts.user",
                    ),
                ),
                (
                    "issuer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="issued_invitations",
                        to="accounts.user",
                    ),
                ),
            ],
            options={
                "verbose_name": "注册邀请",
                "verbose_name_plural": "注册邀请",
                "ordering": ["-created_at", "-pk"],
                "indexes": [
                    models.Index(
                        fields=["claimed_at", "revoked_at", "expires_at"],
                        name="accounts_invitation_state_idx",
                    )
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=(
                            models.Q(("claimed_at__isnull", True), ("claimed_by__isnull", True))
                            | models.Q(
                                ("claimed_at__isnull", False),
                                ("claimed_by__isnull", False),
                            )
                        ),
                        name="accounts_invitation_claim_fields_match",
                    ),
                    models.CheckConstraint(
                        condition=(
                            models.Q(("revoked_at__isnull", True))
                            | models.Q(("claimed_at__isnull", True))
                        ),
                        name="accounts_invitation_not_claimed_and_revoked",
                    ),
                ],
            },
        ),
        migrations.RunPython(create_missing_profiles, migrations.RunPython.noop),
    ]
