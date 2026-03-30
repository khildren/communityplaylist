from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0005_event_latitude_event_longitude'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='category',
            field=models.CharField(
                blank=True,
                choices=[
                    ('music',  'Music'),
                    ('bike',   'Bike'),
                    ('fund',   'Fundraiser'),
                    ('food',   'Food'),
                    ('hybrid', 'Hybrid'),
                ],
                max_length=20,
            ),
        ),
    ]
