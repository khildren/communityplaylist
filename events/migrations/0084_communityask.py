from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0083_auto_communityspace'),
    ]

    operations = [
        migrations.CreateModel(
            name='CommunityAsk',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200)),
                ('description', models.TextField(blank=True)),
                ('ask_type', models.CharField(
                    choices=[
                        ('fund', 'Funding / Donation'),
                        ('item', 'Item / Equipment'),
                        ('volunteer', 'Volunteer Time'),
                        ('skill', 'Skill / Service'),
                    ],
                    default='item', max_length=20,
                )),
                ('target_amount', models.DecimalField(
                    blank=True, decimal_places=0, max_digits=8, null=True,
                    help_text='Funding goal in dollars (optional)',
                )),
                ('donation_url', models.URLField(
                    blank=True,
                    help_text='Specific donate link for this ask — overrides profile donation URL',
                )),
                ('status', models.CharField(
                    choices=[
                        ('open', 'Open'),
                        ('in_progress', 'In Progress'),
                        ('fulfilled', 'Fulfilled — thank you!'),
                    ],
                    default='open', max_length=20,
                )),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('community_space', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='asks',
                    to='events.communityspace',
                )),
                ('venue', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='asks',
                    to='events.venue',
                )),
            ],
            options={
                'verbose_name': 'Community Ask',
                'verbose_name_plural': 'Community Asks',
                'ordering': ['sort_order', 'created_at'],
            },
        ),
    ]
