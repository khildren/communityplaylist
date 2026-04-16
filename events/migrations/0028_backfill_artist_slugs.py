from django.db import migrations
from django.utils.text import slugify


def backfill_slugs(apps, schema_editor):
    Artist = apps.get_model('events', 'Artist')
    for artist in Artist.objects.filter(slug__isnull=True):
        base = slugify(artist.name) or f'artist-{artist.pk}'
        slug, n = base, 1
        while Artist.objects.filter(slug=slug).exists():
            slug = f'{base}-{n}'; n += 1
        artist.slug = slug
        artist.save(update_fields=['slug'])


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0027_artist_promoter_playlist'),
    ]

    operations = [
        migrations.RunPython(backfill_slugs, migrations.RunPython.noop),
    ]
