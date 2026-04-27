from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0080_brand_color'),
    ]

    operations = [
        migrations.AddField(
            model_name='artist',
            name='kofi',
            field=models.CharField(
                blank=True, max_length=100,
                help_text='Ko-fi username e.g. yourname from ko-fi.com/yourname',
            ),
        ),
        migrations.AddField(
            model_name='promoterprofile',
            name='kofi',
            field=models.CharField(
                blank=True, max_length=100,
                help_text='Ko-fi username e.g. yourname from ko-fi.com/yourname',
            ),
        ),
    ]
