from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("database_manager", "0007_sql_record_storage"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="objectrecord",
            index=models.Index(fields=["object", "updated_at"], name="idx_obj_updated_at"),
        ),
    ]

