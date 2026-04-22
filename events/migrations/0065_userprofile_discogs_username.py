from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0064_userprofile_music_services'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='discogs_username',
            field=models.CharField(blank=True, help_text='Discogs username', max_length=100),
        ),
    ]
