from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0072_artist_house_mixes'),
    ]

    operations = [
        migrations.AddField(
            model_name='artist',
            name='house_mixes_sort',
            field=models.CharField(
                blank=True,
                choices=[
                    ('newest', 'Newest first'),
                    ('oldest', 'Oldest first'),
                    ('downloads', 'Most downloaded'),
                    ('plays', 'Most played'),
                ],
                default='newest',
                help_text='Sort order for house-mixes.com track list',
                max_length=20,
            ),
        ),
    ]
