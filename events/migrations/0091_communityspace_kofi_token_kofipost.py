from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0090_communityspace_kofi'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='communityspace',
            name='kofi_token',
            field=models.CharField(
                blank=True, max_length=64, null=True, unique=True,
                help_text='Auto-generated webhook verification token',
            ),
        ),
        migrations.CreateModel(
            name='KofiPost',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kofi_type', models.CharField(
                    choices=[('Donation','Donation'),('Subscription','Subscription'),('Shop_Order','Shop Order'),('Blog_Post','Blog Post')],
                    default='Donation', max_length=20,
                )),
                ('from_name', models.CharField(blank=True, max_length=200)),
                ('message', models.TextField(blank=True)),
                ('url', models.URLField(blank=True, max_length=500)),
                ('is_public', models.BooleanField(default=True)),
                ('amount', models.CharField(blank=True, max_length=20)),
                ('currency', models.CharField(blank=True, max_length=10)),
                ('kofi_transaction_id', models.CharField(blank=True, max_length=120, null=True, unique=True)),
                ('timestamp', models.DateTimeField(blank=True, null=True)),
                ('raw_data', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('community_space', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                    related_name='kofi_posts', to='events.communityspace',
                )),
                ('artist', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='kofi_posts', to='events.artist',
                )),
                ('promoter', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='kofi_posts', to='events.promoterprofile',
                )),
            ],
            options={'ordering': ['-timestamp', '-created_at'], 'verbose_name': 'Ko-fi Post', 'verbose_name_plural': 'Ko-fi Posts'},
        ),
    ]
