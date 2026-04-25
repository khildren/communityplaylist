from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0071_add_artist_admin_email'),
    ]

    operations = [
        migrations.AddField(
            model_name='artist',
            name='house_mixes',
            field=models.CharField(blank=True, help_text='house-mixes.com username', max_length=100),
        ),
    ]
