from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('database_manager', '0012_parameter_is_managed_link_param_parameter_link_meta'),
    ]

    operations = [
        migrations.AddField(
            model_name='parameter',
            name='is_legacy_link_param_deprecated',
            field=models.BooleanField(default=False),
        ),
    ]
