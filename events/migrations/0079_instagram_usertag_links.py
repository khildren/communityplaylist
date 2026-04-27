from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0078_instagram_flyer_scan'),
    ]

    operations = [
        migrations.AddField(
            model_name='instagramaccount',
            name='promoter_profile',
            field=models.OneToOneField(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='instagram_account',
                to='events.promoterprofile',
                help_text='Linked promoter/crew profile',
            ),
        ),
        migrations.AddField(
            model_name='instagramaccount',
            name='artist',
            field=models.OneToOneField(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='instagram_account',
                to='events.artist',
                help_text='Linked artist profile',
            ),
        ),
        migrations.AddField(
            model_name='instagrampost',
            name='tagged_handles',
            field=models.JSONField(
                blank=True, default=list,
                help_text='Instagram handles tagged in this post',
            ),
        ),
    ]
