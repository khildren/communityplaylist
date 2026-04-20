from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0054_artist_beatport'),
    ]

    operations = [
        migrations.AddField(
            model_name='artist',
            name='discogs',
            field=models.URLField(blank=True, help_text='Discogs artist page URL'),
        ),
    ]
