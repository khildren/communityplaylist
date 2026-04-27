from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0079_instagram_usertag_links'),
    ]

    operations = [
        migrations.AddField(
            model_name='artist',
            name='brand_color',
            field=models.CharField(
                blank=True, default='', max_length=7,
                help_text='Profile accent hex color e.g. #ff6b35 — leave blank for default orange',
            ),
        ),
        migrations.AddField(
            model_name='promoterprofile',
            name='brand_color',
            field=models.CharField(
                blank=True, default='', max_length=7,
                help_text='Profile accent hex color e.g. #ff6b35 — leave blank for default orange',
            ),
        ),
        migrations.AddField(
            model_name='venue',
            name='brand_color',
            field=models.CharField(
                blank=True, default='', max_length=7,
                help_text='Profile accent hex color e.g. #ff6b35 — leave blank for default orange',
            ),
        ),
    ]
