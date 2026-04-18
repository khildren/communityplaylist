from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0048_userprofile_v06'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='userprofile',
            name='messenger_url',
        ),
        migrations.AddField(
            model_name='userprofile',
            name='messenger_telegram',
            field=models.CharField(blank=True, max_length=100,
                                   help_text='Telegram handle without @'),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='messenger_discord',
            field=models.CharField(blank=True, max_length=30,
                                   help_text='Discord user ID (numeric)'),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='messenger_signal',
            field=models.CharField(blank=True, max_length=100,
                                   help_text='Signal username'),
        ),
    ]
