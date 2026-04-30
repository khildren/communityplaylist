from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0098_artist_enrichment_locked'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='youtube_playlist',
            field=models.URLField(blank=True, help_text='YouTube playlist URL for this event — shown on event page (e.g. https://youtube.com/playlist?list=PLxxxx)'),
        ),
    ]
