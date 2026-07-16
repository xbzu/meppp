import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("publishing", "0006_video_asset")]

    operations = [
        migrations.AlterField(
            model_name="entry",
            name="body",
            field=models.TextField(
                blank=True,
                validators=[
                    django.core.validators.MinLengthValidator(1),
                    django.core.validators.MaxLengthValidator(5_000),
                ],
            ),
        ),
    ]
