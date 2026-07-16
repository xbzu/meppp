from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models

MAX_IMAGES_PER_POST = 4
MAX_IMAGE_UPLOAD_BYTES = 5 * 1024 * 1024


def clamp_existing_image_limits(apps, schema_editor):
    SiteConfiguration = apps.get_model("configuration", "SiteConfiguration")
    ConfigurationRevision = apps.get_model("configuration", "ConfigurationRevision")
    database = schema_editor.connection.alias

    for configuration in SiteConfiguration.objects.using(database).all().iterator():
        safe_images = min(configuration.max_images_per_post, MAX_IMAGES_PER_POST)
        safe_bytes = min(configuration.upload_max_bytes, MAX_IMAGE_UPLOAD_BYTES)
        if (
            safe_images == configuration.max_images_per_post
            and safe_bytes == configuration.upload_max_bytes
        ):
            continue

        latest_revision = (
            ConfigurationRevision.objects.using(database)
            .order_by("-version")
            .values_list("version", flat=True)
            .first()
            or 0
        )
        configuration.max_images_per_post = safe_images
        configuration.upload_max_bytes = safe_bytes
        configuration.version = max(configuration.version, latest_revision) + 1
        configuration.save(
            using=database,
            update_fields=("max_images_per_post", "upload_max_bytes", "version", "updated_at"),
        )
        ConfigurationRevision.objects.using(database).create(
            version=configuration.version,
            snapshot={
                "site_name": configuration.site_name,
                "tagline": configuration.tagline,
                "registration_mode": configuration.registration_mode,
                "post_max_length": configuration.post_max_length,
                "comment_max_length": configuration.comment_max_length,
                "max_images_per_post": configuration.max_images_per_post,
                "upload_max_bytes": configuration.upload_max_bytes,
                "moderation_mode": configuration.moderation_mode,
                "comments_enabled": configuration.comments_enabled,
            },
            actor=None,
            reason="Security migration: clamp image upload limits to the server hard caps",
        )


class Migration(migrations.Migration):
    dependencies = [("configuration", "0002_alter_configurationrevision_options")]

    operations = [
        migrations.RunPython(clamp_existing_image_limits, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="siteconfiguration",
            name="max_images_per_post",
            field=models.PositiveSmallIntegerField(
                default=MAX_IMAGES_PER_POST,
                validators=[MinValueValidator(0), MaxValueValidator(MAX_IMAGES_PER_POST)],
            ),
        ),
        migrations.AlterField(
            model_name="siteconfiguration",
            name="upload_max_bytes",
            field=models.PositiveIntegerField(
                default=MAX_IMAGE_UPLOAD_BYTES,
                validators=[MinValueValidator(131072), MaxValueValidator(MAX_IMAGE_UPLOAD_BYTES)],
            ),
        ),
        migrations.AddConstraint(
            model_name="siteconfiguration",
            constraint=models.CheckConstraint(
                condition=models.Q(max_images_per_post__lte=MAX_IMAGES_PER_POST),
                name="configuration_image_count_hard_cap",
            ),
        ),
        migrations.AddConstraint(
            model_name="siteconfiguration",
            constraint=models.CheckConstraint(
                condition=models.Q(upload_max_bytes__lte=MAX_IMAGE_UPLOAD_BYTES),
                name="configuration_image_bytes_hard_cap",
            ),
        ),
    ]
