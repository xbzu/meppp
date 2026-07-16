import django.core.validators
from django.db import migrations, models

import meppp.publishing.models


def require_empty_legacy_attachment_table(apps, schema_editor):
    Attachment = apps.get_model("publishing", "Attachment")
    database = schema_editor.connection.alias
    legacy_count = Attachment.objects.using(database).count()
    if legacy_count:
        raise RuntimeError(
            "Secure media migration 0005 requires the legacy attachment table to be empty; "
            f"found {legacy_count} untrusted row(s). Earlier releases did not guarantee server "
            "re-encoding. The migration changed nothing: back up the database and media, inspect "
            "or quarantine every legacy attachment with the previous release, then retry."
        )


class Migration(migrations.Migration):
    dependencies = [("publishing", "0004_pendingcomment_pendingentry_contentreviewdecision")]

    operations = [
        migrations.RunPython(require_empty_legacy_attachment_table, migrations.RunPython.noop),
        migrations.AlterModelOptions(
            name="attachment",
            options={
                "base_manager_name": "objects",
                "ordering": ["position", "created_at"],
                "verbose_name": "图片附件",
                "verbose_name_plural": "图片附件",
            },
        ),
        migrations.AlterField(
            model_name="attachment",
            name="byte_size",
            field=models.PositiveIntegerField(editable=False),
        ),
        migrations.AlterField(
            model_name="attachment",
            name="file",
            field=models.FileField(
                upload_to=meppp.publishing.models.attachment_upload_path,
                validators=[django.core.validators.FileExtensionValidator(["webp"])],
            ),
        ),
        migrations.AlterField(
            model_name="attachment",
            name="height",
            field=models.PositiveIntegerField(blank=True, editable=False, null=True),
        ),
        migrations.AlterField(
            model_name="attachment",
            name="mime_type",
            field=models.CharField(
                choices=[("image/webp", "WebP")],
                default="image/webp",
                editable=False,
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="attachment",
            name="position",
            field=models.PositiveSmallIntegerField(default=0, editable=False),
        ),
        migrations.AlterField(
            model_name="attachment",
            name="width",
            field=models.PositiveIntegerField(blank=True, editable=False, null=True),
        ),
        migrations.AddConstraint(
            model_name="attachment",
            constraint=models.CheckConstraint(
                condition=models.Q(("mime_type", "image/webp")),
                name="publishing_attachment_mime_webp",
            ),
        ),
        migrations.AddConstraint(
            model_name="attachment",
            constraint=models.CheckConstraint(
                condition=models.Q(("width__gt", 0), ("width__isnull", False)),
                name="publishing_attachment_width_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="attachment",
            constraint=models.CheckConstraint(
                condition=models.Q(("height__gt", 0), ("height__isnull", False)),
                name="publishing_attachment_height_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="attachment",
            constraint=models.CheckConstraint(
                condition=models.Q(("file__endswith", ".webp")),
                name="publishing_attachment_file_webp",
            ),
        ),
    ]
