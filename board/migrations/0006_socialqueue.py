from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('board', '0005_postreport'),
    ]

    operations = [
        migrations.CreateModel(
            name='SocialQueue',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('target_type', models.CharField(choices=[('topic', 'Topic'), ('offering', 'Offering'), ('event', 'Event')], max_length=20)),
                ('target_id', models.PositiveIntegerField()),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('posted', 'Posted'), ('failed', 'Failed'), ('skipped', 'Skipped')], default='queued', max_length=10)),
                ('post_after', models.DateTimeField()),
                ('bluesky_uri', models.CharField(blank=True, max_length=200)),
                ('error', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('posted_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={'ordering': ['post_after']},
        ),
    ]
