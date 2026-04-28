from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0091_communityspace_kofi_token_kofipost'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SupportTicket',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ticket_type', models.CharField(
                    choices=[('idea','💡 Idea / Feature'),('bug','🐛 Bug Report'),('venue','🏛 Add a Venue'),('space','🌱 Add a Space'),('other','💬 General Message')],
                    default='other', max_length=20,
                )),
                ('subject',     models.CharField(max_length=200)),
                ('body',        models.TextField()),
                ('from_name',   models.CharField(blank=True, max_length=120)),
                ('from_email',  models.EmailField(blank=True)),
                ('status',      models.CharField(
                    choices=[('open','Open'),('in_progress','In Progress'),('closed','Closed')],
                    default='open', max_length=20,
                )),
                ('admin_notes', models.TextField(blank=True)),
                ('created_at',  models.DateTimeField(auto_now_add=True)),
                ('updated_at',  models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='support_tickets', to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['-created_at'], 'verbose_name': 'Support Ticket', 'verbose_name_plural': 'Support Tickets'},
        ),
    ]
