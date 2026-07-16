from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("configuration", "0003_tighten_image_upload_limit")]

    operations = [
        migrations.AddField(
            model_name="siteconfiguration",
            name="video_uploads_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="x_references_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="siteconfiguration",
            name="youtube_references_enabled",
            field=models.BooleanField(default=True),
        ),
    ]
