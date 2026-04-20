from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0051_shelter_model'),
    ]

    operations = [
        migrations.AddField(
            model_name='artist',
            name='is_stub',
            field=models.BooleanField(default=False, help_text='Auto-generated from events — not yet claimed'),
        ),
        migrations.AddField(
            model_name='artist',
            name='city',
            field=models.CharField(blank=True, max_length=100, help_text='Derived from event venues or platform profile'),
        ),
        migrations.AddField(
            model_name='artist',
            name='latitude',
            field=models.FloatField(blank=True, null=True, help_text='Geo center derived from event venue cluster'),
        ),
        migrations.AddField(
            model_name='artist',
            name='longitude',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='artist',
            name='home_neighborhood',
            field=models.CharField(blank=True, max_length=100, help_text='Most frequent event neighborhood'),
        ),
        migrations.AddField(
            model_name='artist',
            name='auto_bio',
            field=models.TextField(blank=True, help_text='System-generated bio from event history — replaced by artist bio on claim'),
        ),
        migrations.AddField(
            model_name='artist',
            name='last_enriched_at',
            field=models.DateTimeField(blank=True, null=True, help_text='Last time enrichment was run for this artist'),
        ),
    ]
