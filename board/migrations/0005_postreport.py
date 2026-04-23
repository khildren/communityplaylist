from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('board', '0004_offering_poster_user'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PostReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('target_type', models.CharField(choices=[('topic', 'Topic'), ('reply', 'Reply'), ('offering', 'Offering')], max_length=20)),
                ('target_id', models.PositiveIntegerField()),
                ('reason', models.CharField(choices=[('spam', 'Spam or scam'), ('harmful', 'Inappropriate or harmful content'), ('wrong_section', 'Posted in the wrong section'), ('misinfo', 'Misinformation'), ('other', 'Other')], max_length=30)),
                ('note', models.TextField(blank=True, max_length=500)),
                ('reporter_ip', models.GenericIPAddressField(blank=True, null=True)),
                ('resolved', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('reporter_user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='post_reports', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
