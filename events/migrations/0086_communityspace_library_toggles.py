from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0085_communityask_product_board'),
    ]

    operations = [
        migrations.AddField(
            model_name='communityspace',
            name='show_audio',
            field=models.BooleanField(
                default=False,
                help_text='Display audio files from the Drive folder as an inline player on the profile',
            ),
        ),
        migrations.AddField(
            model_name='communityspace',
            name='show_docs',
            field=models.BooleanField(
                default=False,
                help_text='Display PDFs / Google Docs / zines from the Drive folder as a document library on the profile',
            ),
        ),
    ]
