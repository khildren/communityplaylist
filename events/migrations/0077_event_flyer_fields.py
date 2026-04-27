from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0076_videoroomessage'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='flyer_url',
            field=models.URLField(blank=True, help_text='Instagram post URL or direct flyer image URL — used for AI enrichment'),
        ),
        migrations.AddField(
            model_name='event',
            name='flyer_scanned',
            field=models.BooleanField(default=False, help_text='Set once moondream has scanned this flyer'),
        ),
    ]
