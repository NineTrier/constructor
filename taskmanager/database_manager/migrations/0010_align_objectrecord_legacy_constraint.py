from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("database_manager", "0009_object_match_keys"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="objectrecord",
            name="uniq_object_legacy_id_to_connect",
        ),
        migrations.AddConstraint(
            model_name="objectrecord",
            constraint=models.UniqueConstraint(
                condition=~models.Q(legacy_id_to_connect=None),
                fields=["object", "legacy_id_to_connect"],
                name="uniq_object_legacy_id_to_connect",
            ),
        ),
    ]
