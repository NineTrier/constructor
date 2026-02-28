from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("database_manager", "0008_objectrecord_updated_at_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="object",
            name="match_keys",
            field=models.JSONField(blank=True, default=list),
        ),
    ]

