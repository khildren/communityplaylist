from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0099_event_youtube_playlist'),
    ]

    operations = [
        migrations.AddField(
            model_name='videotrack',
            name='yt_embeddable',
            field=models.BooleanField(default=True, help_text='False when YouTube owner has disabled embedding — never shown in inline players'),
        ),
    ]
