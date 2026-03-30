from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0010_calendarfeed'),
    ]

    operations = [
        migrations.CreateModel(
            name='VenueFeed',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('website', models.URLField(blank=True, max_length=500)),
                ('url', models.URLField(blank=True, max_length=500, help_text='iCal feed URL (required for iCal source)')),
                ('source_type', models.CharField(choices=[('ical', 'iCal Feed'), ('eventbrite', 'Eventbrite API')], default='ical', max_length=20)),
                ('active', models.BooleanField(default=True)),
                ('auto_approve', models.BooleanField(default=False, help_text='Publish events immediately without manual review')),
                ('default_category', models.CharField(blank=True, choices=[('', 'Auto-detect'), ('music', 'Music'), ('bike', 'Bike'), ('fund', 'Fundraiser'), ('food', 'Food'), ('hybrid', 'Hybrid')], max_length=20)),
                ('last_synced', models.DateTimeField(blank=True, null=True)),
                ('last_error', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('notes', models.TextField(blank=True, help_text='Internal notes about this source')),
            ],
            options={'ordering': ['name']},
        ),
    ]
