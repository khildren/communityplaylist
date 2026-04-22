from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('board', '0002_topic_neighborhood'),
        ('events', '0067_userplaylist'),
    ]

    operations = [
        migrations.CreateModel(
            name='Offering',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200)),
                ('body', models.TextField(blank=True, help_text='Describe the item — condition, size, pickup info…')),
                ('category', models.CharField(
                    choices=[('give', 'Free — Take It'), ('trade', 'Trade / Swap'), ('iso', 'In Search Of')],
                    default='give', max_length=10,
                )),
                ('photo', models.ImageField(blank=True, null=True, upload_to='offerings/')),
                ('contact_hint', models.CharField(
                    blank=True, max_length=200,
                    help_text='How to reach you — e.g. "reply to this thread" or "DM on IG @handle"',
                )),
                ('author_name', models.CharField(max_length=80)),
                ('poster_ip', models.GenericIPAddressField(blank=True, null=True)),
                ('is_claimed', models.BooleanField(default=False)),
                ('claimed_at', models.DateTimeField(blank=True, null=True)),
                ('active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField()),
                ('board_topic', models.OneToOneField(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='offering',
                    to='board.topic',
                )),
                ('neighborhood', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='offerings',
                    to='events.neighborhood',
                )),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
