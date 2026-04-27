from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0086_communityspace_library_toggles'),
    ]

    operations = [
        migrations.AddField(
            model_name='artist',
            name='allow_comments',
            field=models.BooleanField(default=False, help_text='Allow public comments on profile page'),
        ),
        migrations.AddField(
            model_name='promoterprofile',
            name='allow_comments',
            field=models.BooleanField(default=False, help_text='Allow public comments on profile page'),
        ),
        migrations.AddField(
            model_name='communityspace',
            name='allow_comments',
            field=models.BooleanField(default=False, help_text='Allow public comments on profile page'),
        ),
        migrations.AddField(
            model_name='venue',
            name='allow_comments',
            field=models.BooleanField(default=False, help_text='Allow public comments on venue page'),
        ),
    ]
