from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0007_sitestats'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='extra_links',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
