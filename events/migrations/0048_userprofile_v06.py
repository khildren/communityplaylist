from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0047_promoter_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='wants_artist',
            field=models.BooleanField(default=False, help_text='User has or wants an artist profile'),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='wants_promoter',
            field=models.BooleanField(default=False, help_text='User has or wants a crew/promoter profile'),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='wants_venue',
            field=models.BooleanField(default=False, help_text='User has or wants a venue profile'),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='messenger_url',
            field=models.URLField(blank=True, help_text='Link to preferred messenger — Telegram, Discord, Signal, etc.'),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='sol_wallet',
            field=models.CharField(blank=True, max_length=120, help_text='Solana wallet address (Phantom public key, etc.)'),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='onboarded',
            field=models.BooleanField(default=False, help_text='Completed post-signup profile type picker'),
        ),
    ]
