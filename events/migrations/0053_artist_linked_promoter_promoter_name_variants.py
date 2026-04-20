from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0052_artist_enrichment_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='artist',
            name='linked_promoter',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='linked_artists',
                to='events.promoterprofile',
                help_text='If this artist record is also a crew/collective, link their PromoterProfile here',
            ),
        ),
        migrations.AddField(
            model_name='promoterprofile',
            name='name_variants',
            field=models.TextField(
                blank=True,
                help_text='Pipe-separated name aliases that should resolve to this profile '
                          '(e.g. "Subduction Audio & Friends|Subduction Audio Crew"). '
                          'Used by the event parser to consolidate mismatched listings.',
            ),
        ),
    ]
