from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0077_event_flyer_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='instagramaccount',
            name='harvest_for_events',
            field=models.BooleanField(
                default=False,
                help_text='Run moondream flyer scan on new posts — creates pending Events from detected flyers',
            ),
        ),
        migrations.AddField(
            model_name='instagrampost',
            name='flyer_scanned',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='instagrampost',
            name='flyer_result',
            field=models.JSONField(blank=True, null=True,
                                   help_text='Raw moondream output — dict of extracted event fields'),
        ),
        migrations.AddField(
            model_name='instagrampost',
            name='sourced_event',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='instagram_sources',
                to='events.event',
                help_text='Event created from this post by flyer scan',
            ),
        ),
    ]
