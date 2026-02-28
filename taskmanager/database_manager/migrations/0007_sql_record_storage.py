from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("database_manager", "0006_alter_object_options"),
    ]

    operations = [
        migrations.CreateModel(
            name="ObjectRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("record_uid", models.CharField(max_length=64)),
                ("legacy_id_to_connect", models.CharField(blank=True, max_length=255, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "object",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="records",
                        to="database_manager.object",
                    ),
                ),
            ],
            options={
                "ordering": ["id"],
            },
        ),
        migrations.CreateModel(
            name="RecordLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "child_record",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="incoming_links",
                        to="database_manager.objectrecord",
                    ),
                ),
                (
                    "object_link",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="record_links",
                        to="database_manager.object_parentobject",
                    ),
                ),
                (
                    "parent_record",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="outgoing_links",
                        to="database_manager.objectrecord",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ParameterValue",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("value_text", models.TextField(blank=True, null=True)),
                ("value_int", models.BigIntegerField(blank=True, null=True)),
                ("value_datetime", models.DateTimeField(blank=True, null=True)),
                ("value_json", models.JSONField(blank=True, null=True)),
                (
                    "parameter",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="record_values",
                        to="database_manager.parameter",
                    ),
                ),
                (
                    "record",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="parameter_values",
                        to="database_manager.objectrecord",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="objectrecord",
            constraint=models.UniqueConstraint(fields=("object", "record_uid"), name="uniq_object_record_uid"),
        ),
        migrations.AddConstraint(
            model_name="objectrecord",
            constraint=models.UniqueConstraint(
                condition=models.Q(legacy_id_to_connect__isnull=False),
                fields=("object", "legacy_id_to_connect"),
                name="uniq_object_legacy_id_to_connect",
            ),
        ),
        migrations.AddIndex(
            model_name="objectrecord",
            index=models.Index(fields=["object", "record_uid"], name="idx_obj_record_uid"),
        ),
        migrations.AddIndex(
            model_name="objectrecord",
            index=models.Index(fields=["object", "legacy_id_to_connect"], name="idx_obj_legacy_id"),
        ),
        migrations.AddConstraint(
            model_name="parametervalue",
            constraint=models.UniqueConstraint(fields=("record", "parameter"), name="uniq_record_parameter_value"),
        ),
        migrations.AddIndex(
            model_name="parametervalue",
            index=models.Index(fields=["parameter"], name="idx_param_value_param"),
        ),
        migrations.AddIndex(
            model_name="parametervalue",
            index=models.Index(fields=["record"], name="idx_param_value_record"),
        ),
        migrations.AddConstraint(
            model_name="recordlink",
            constraint=models.UniqueConstraint(
                fields=("object_link", "parent_record", "child_record"),
                name="uniq_record_link_tuple",
            ),
        ),
        migrations.AddIndex(
            model_name="recordlink",
            index=models.Index(fields=["object_link"], name="idx_record_link_link"),
        ),
        migrations.AddIndex(
            model_name="recordlink",
            index=models.Index(fields=["parent_record"], name="idx_record_link_parent"),
        ),
        migrations.AddIndex(
            model_name="recordlink",
            index=models.Index(fields=["child_record"], name="idx_record_link_child"),
        ),
    ]
