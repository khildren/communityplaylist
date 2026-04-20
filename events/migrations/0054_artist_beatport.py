from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0053_artist_linked_promoter_promoter_name_variants'),
    ]

    operations = [
        migrations.AddField(
            model_name='artist',
            name='beatport',
            field=models.URLField(blank=True, help_text='Beatport artist page URL'),
        ),
    ]
