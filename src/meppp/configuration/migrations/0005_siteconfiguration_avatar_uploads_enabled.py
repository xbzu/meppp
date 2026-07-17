from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("configuration", "0004_site_feature_switches"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfiguration",
            name="avatar_uploads_enabled",
            field=models.BooleanField(default=True),
        ),
    ]
