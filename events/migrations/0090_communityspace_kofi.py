from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0089_spacephoto_spaceupdate'),
    ]

    operations = [
        migrations.AddField(
            model_name='communityspace',
            name='kofi',
            field=models.CharField(
                blank=True,
                max_length=100,
                help_text='Ko-fi page ID or username e.g. U7U813CDB7 from ko-fi.com/U7U813CDB7',
            ),
        ),
    ]
