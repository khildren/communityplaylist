from django.db import migrations, models
import django.db.models.deletion
from django.utils.text import slugify


def generate_slugs(apps, schema_editor):
    Event = apps.get_model('events', 'Event')
    seen = set()
    for event in Event.objects.all():
        base = slugify(event.title) or 'event'
        slug = base
        counter = 1
        while slug in seen:
            slug = f"{base}-{counter}"
            counter += 1
        seen.add(slug)
        event.slug = slug
        event.save()


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='slug',
            field=models.SlugField(blank=True, max_length=220, null=True),
        ),
        migrations.RunPython(generate_slugs),
        migrations.AlterField(
            model_name='event',
            name='slug',
            field=models.SlugField(blank=True, max_length=220, unique=True),
        ),
        migrations.CreateModel(
            name='EventPhoto',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image', models.ImageField(upload_to='event_photos/%Y/%m/')),
                ('caption', models.CharField(blank=True, max_length=200)),
                ('photo_type', models.CharField(choices=[('promo', 'Promo / Flyer'), ('recap', 'Event Recap')], default='promo', max_length=10)),
                ('submitted_by', models.CharField(blank=True, max_length=100)),
                ('submitted_email', models.EmailField(blank=True, max_length=254)),
                ('approved', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('event', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='photos', to='events.event')),
            ],
            options={'ordering': ['created_at']},
        ),
    ]