from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0093_trackreaction'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TrackComment',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('body', models.TextField(max_length=500)),
                ('ts', models.PositiveIntegerField(default=0, help_text='Playback position in seconds')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('track', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='comments', to='events.playlisttrack')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='track_comments', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['ts', 'created_at'],
            },
        ),
    ]
