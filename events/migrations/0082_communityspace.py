from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0081_artist_promoter_kofi'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CommunitySpace',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('slug', models.SlugField(blank=True, max_length=220, unique=True)),
                ('space_type', models.CharField(
                    choices=[
                        ('garden', 'Community Garden'),
                        ('third_space', 'Third Space'),
                        ('makerspace', 'Makerspace / Hackerspace'),
                        ('library', 'Free Library'),
                        ('park', 'Park / Outdoor Space'),
                    ],
                    default='garden', max_length=30,
                )),
                ('bio', models.TextField(blank=True)),
                ('photo', models.ImageField(blank=True, null=True, upload_to='community_spaces/')),
                ('brand_color', models.CharField(
                    blank=True, default='',
                    help_text='Profile accent hex color e.g. #4caf50 — leave blank for default green',
                    max_length=7,
                )),
                ('address', models.CharField(blank=True, max_length=300)),
                ('neighborhood', models.CharField(blank=True, max_length=100)),
                ('latitude', models.FloatField(blank=True, null=True)),
                ('longitude', models.FloatField(blank=True, null=True)),
                ('contact_email', models.EmailField(
                    blank=True,
                    help_text='Displayed on profile — use a public contact address',
                    max_length=254,
                )),
                ('website', models.URLField(blank=True)),
                ('instagram', models.CharField(blank=True, help_text='Handle without @', max_length=100)),
                ('bluesky', models.CharField(blank=True, help_text='Handle e.g. you.bsky.social', max_length=100)),
                ('mastodon', models.URLField(blank=True, help_text='Full profile URL e.g. https://pdx.social/@you')),
                ('tiktok', models.CharField(blank=True, help_text='Handle without @', max_length=100)),
                ('drive_folder_url', models.URLField(
                    blank=True,
                    help_text='Public Google Drive folder — shows as "Resource Library" button',
                )),
                ('sol_wallet', models.CharField(
                    blank=True,
                    help_text='Solana wallet address (Phantom) — shows a ♥ Donate button',
                    max_length=120,
                )),
                ('donation_url', models.URLField(
                    blank=True,
                    help_text='Ko-fi, Open Collective, or Helium link — shown alongside SOL wallet',
                )),
                ('custom_links', models.JSONField(
                    blank=True, default=list,
                    help_text='Up to 8 custom buttons: [{"label": "Code of Conduct", "url": "https://...", "thumbnail_url": ""}]',
                )),
                ('is_verified', models.BooleanField(default=False)),
                ('is_public', models.BooleanField(default=True)),
                ('view_count', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('claimed_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='claimed_spaces',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['name'], 'verbose_name': 'Community Space', 'verbose_name_plural': 'Community Spaces'},
        ),
        migrations.AlterField(
            model_name='follow',
            name='target_type',
            field=models.CharField(
                choices=[
                    ('artist', 'Artist'),
                    ('venue', 'Venue'),
                    ('neighborhood', 'Neighborhood'),
                    ('promoter', 'Promoter'),
                    ('space', 'Community Space'),
                ],
                max_length=20,
            ),
        ),
    ]
