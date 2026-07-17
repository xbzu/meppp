from django.core.validators import FileExtensionValidator
from django.db import migrations, models


def assert_no_unverified_legacy_avatars(apps, schema_editor):
    profile = apps.get_model("accounts", "Profile")
    if profile.objects.exclude(avatar="").exists():
        raise RuntimeError(
            "Unverified legacy profile avatars exist; normalize them offline before migrating."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_recoverycredential"),
    ]

    operations = [
        migrations.AlterField(
            model_name="profile",
            name="avatar",
            field=models.FileField(
                blank=True,
                upload_to="avatars/",
                validators=[FileExtensionValidator(["webp"])],
            ),
        ),
        migrations.AddField(
            model_name="profile",
            name="avatar_version",
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="profile",
            name="avatar_byte_size",
            field=models.PositiveIntegerField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="profile",
            name="avatar_width",
            field=models.PositiveIntegerField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="profile",
            name="avatar_height",
            field=models.PositiveIntegerField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(
            assert_no_unverified_legacy_avatars,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="profile",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        avatar="",
                        avatar_version__isnull=True,
                        avatar_byte_size__isnull=True,
                        avatar_width__isnull=True,
                        avatar_height__isnull=True,
                    )
                    | (
                        ~models.Q(avatar="")
                        & models.Q(
                            avatar_version__isnull=False,
                            avatar_byte_size__isnull=False,
                            avatar_byte_size__gt=0,
                            avatar_width__isnull=False,
                            avatar_width__gt=0,
                            avatar_height__isnull=False,
                            avatar_height__gt=0,
                        )
                    )
                ),
                name="accounts_profile_avatar_metadata_match",
            ),
        ),
        migrations.AddConstraint(
            model_name="profile",
            constraint=models.CheckConstraint(
                condition=models.Q(avatar="") | models.Q(avatar__endswith=".webp"),
                name="accounts_profile_avatar_webp",
            ),
        ),
    ]
