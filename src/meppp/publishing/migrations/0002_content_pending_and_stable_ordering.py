from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("publishing", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="attachment",
            name="alt_text",
            field=models.CharField(blank=True, max_length=240),
        ),
        migrations.AlterModelOptions(
            name="comment",
            options={"base_manager_name": "objects", "ordering": ["created_at", "pk"]},
        ),
        migrations.AlterModelOptions(
            name="entry",
            options={"base_manager_name": "objects", "ordering": ["-created_at", "-pk"]},
        ),
        migrations.AlterField(
            model_name="comment",
            name="state",
            field=models.CharField(
                choices=[
                    ("pending", "待审核"),
                    ("published", "已发布"),
                    ("hidden", "已隐藏"),
                    ("deleted", "已删除"),
                ],
                default="published",
                max_length=12,
            ),
        ),
        migrations.AlterField(
            model_name="entry",
            name="state",
            field=models.CharField(
                choices=[
                    ("pending", "待审核"),
                    ("published", "已发布"),
                    ("hidden", "已隐藏"),
                    ("deleted", "已删除"),
                ],
                default="published",
                max_length=12,
            ),
        ),
    ]
