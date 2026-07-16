import uuid

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models

import meppp.publishing.models


class Migration(migrations.Migration):
    dependencies = [("publishing", "0005_secure_attachment_contract")]

    operations = [
        migrations.CreateModel(
            name="VideoAsset",
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
                (
                    "file",
                    models.FileField(
                        upload_to=meppp.publishing.models.video_upload_path,
                        validators=[django.core.validators.FileExtensionValidator(["mp4", "webm"])],
                    ),
                ),
                (
                    "poster",
                    models.FileField(
                        upload_to=meppp.publishing.models.video_poster_upload_path,
                        validators=[django.core.validators.FileExtensionValidator(["webp"])],
                    ),
                ),
                (
                    "mime_type",
                    models.CharField(
                        choices=[("video/mp4", "MP4"), ("video/webm", "WebM")],
                        editable=False,
                        max_length=10,
                    ),
                ),
                ("byte_size", models.PositiveIntegerField(editable=False)),
                ("poster_byte_size", models.PositiveIntegerField(editable=False)),
                ("duration_ms", models.PositiveIntegerField(editable=False)),
                ("width", models.PositiveIntegerField(editable=False)),
                ("height", models.PositiveIntegerField(editable=False)),
                (
                    "entry",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="video",
                        to="publishing.entry",
                    ),
                ),
            ],
            options={
                "verbose_name": "视频附件",
                "verbose_name_plural": "视频附件",
                "ordering": ["created_at", "pk"],
                "base_manager_name": "objects",
            },
        ),
        migrations.AddConstraint(
            model_name="videoasset",
            constraint=models.CheckConstraint(
                condition=models.Q(byte_size__gt=0, byte_size__lte=20 * 1024 * 1024),
                name="publishing_video_size_within_cap",
            ),
        ),
        migrations.AddConstraint(
            model_name="videoasset",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    poster_byte_size__gt=0,
                    poster_byte_size__lte=2 * 1024 * 1024,
                ),
                name="publishing_video_poster_size_within_cap",
            ),
        ),
        migrations.AddConstraint(
            model_name="videoasset",
            constraint=models.CheckConstraint(
                condition=models.Q(duration_ms__gt=0, duration_ms__lte=5 * 60 * 1000),
                name="publishing_video_duration_within_cap",
            ),
        ),
        migrations.AddConstraint(
            model_name="videoasset",
            constraint=models.CheckConstraint(
                condition=models.Q(width__gt=0),
                name="publishing_video_width_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="videoasset",
            constraint=models.CheckConstraint(
                condition=models.Q(height__gt=0),
                name="publishing_video_height_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="videoasset",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(mime_type="video/mp4", file__endswith=".mp4")
                    | models.Q(mime_type="video/webm", file__endswith=".webm")
                ),
                name="publishing_video_mime_matches_file",
            ),
        ),
        migrations.AddConstraint(
            model_name="videoasset",
            constraint=models.CheckConstraint(
                condition=models.Q(poster__endswith="-poster.webp"),
                name="publishing_video_poster_webp",
            ),
        ),
    ]
