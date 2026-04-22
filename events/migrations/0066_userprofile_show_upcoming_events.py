from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0065_userprofile_discogs_username'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='show_upcoming_events',
            field=models.BooleanField(default=True, help_text='Show upcoming events on public profile'),
        ),
    ]
