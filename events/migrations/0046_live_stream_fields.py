from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0045_twitch_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='artist',
            name='is_live',
            field=models.BooleanField(default=False, help_text='Currently streaming live (updated by check_live_streams)'),
        ),
        migrations.AddField(
            model_name='artist',
            name='youtube_channel_id',
            field=models.CharField(blank=True, help_text='Cached YouTube channel ID (UCxxx\u2026)', max_length=50),
        ),
        migrations.AddField(
            model_name='promoterprofile',
            name='is_live',
            field=models.BooleanField(default=False, help_text='Currently streaming live (updated by check_live_streams)'),
        ),
        migrations.AddField(
            model_name='promoterprofile',
            name='youtube_channel_id',
            field=models.CharField(blank=True, help_text='Cached YouTube channel ID (UCxxx\u2026)', max_length=50),
        ),
        migrations.AddField(
            model_name='venue',
            name='is_live',
            field=models.BooleanField(default=False, help_text='Currently streaming live (updated by check_live_streams)'),
        ),
        migrations.AddField(
            model_name='venue',
            name='youtube_channel_id',
            field=models.CharField(blank=True, help_text='Cached YouTube channel ID (UCxxx\u2026)', max_length=50),
        ),
    ]
