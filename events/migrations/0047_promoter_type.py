from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0046_live_stream_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='promoterprofile',
            name='promoter_type',
            field=models.CharField(
                choices=[
                    ('crew',        'Crew'),
                    ('sound_system','Sound System'),
                    ('collective',  'Collective'),
                    ('label',       'Record Label'),
                    ('record_swap', 'Record Swap'),
                ],
                default='crew',
                help_text='What kind of entity is this?',
                max_length=20,
            ),
        ),
    ]
