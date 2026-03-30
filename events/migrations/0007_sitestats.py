from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0006_event_category'),
    ]

    operations = [
        migrations.CreateModel(
            name='SiteStats',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('visit_count', models.BigIntegerField(default=0)),
            ],
            options={
                'verbose_name_plural': 'site stats',
            },
        ),
    ]
