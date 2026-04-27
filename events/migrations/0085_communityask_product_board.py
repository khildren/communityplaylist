from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('board', '0007_alter_offering_id_alter_topic_category'),
        ('events', '0084_communityask'),
    ]

    operations = [
        migrations.AddField(
            model_name='communityask',
            name='product_url',
            field=models.URLField(blank=True, help_text='Link to specific item on Amazon or elsewhere'),
        ),
        migrations.AddField(
            model_name='communityask',
            name='product_image_url',
            field=models.URLField(blank=True, help_text='Product thumbnail URL'),
        ),
        migrations.AddField(
            model_name='communityask',
            name='product_price',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=8, null=True,
                help_text='Approximate cost in dollars',
            ),
        ),
        migrations.AddField(
            model_name='communityask',
            name='board_offering',
            field=models.OneToOneField(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='community_ask',
                to='board.offering',
                help_text='Living Buy Nothing ISO post linked to this ask',
            ),
        ),
    ]
