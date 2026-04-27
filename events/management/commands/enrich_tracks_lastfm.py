"""
enrich_tracks_lastfm — backfill genre on PlaylistTrack records using Last.fm artist tags.

For each unique artist_name that has tracks missing a genre FK, queries Last.fm's
artist.getTopTags endpoint and assigns the best matching Genre (creating it if needed).

Last.fm tags are noisy, so only the top tag is used and only if it scores ≥ 30/100.

Run:
    python manage.py enrich_tracks_lastfm
    python manage.py enrich_tracks_lastfm --artist "Binsky"
    python manage.py enrich_tracks_lastfm --dry-run
    python manage.py enrich_tracks_lastfm --force      # re-tag even if genre already set
    python manage.py enrich_tracks_lastfm --min-score 50
"""
import time
import urllib.request
import urllib.parse
import json
from django.core.management.base import BaseCommand
from django.conf import settings
from events.models import PlaylistTrack, Genre

LASTFM_API = 'https://ws.audioscrobbler.com/2.0/'

# Tags to skip — too generic or not music genres
SKIP_TAGS = {
    'seen live', 'favourite', 'favorites', 'favourites', 'love', 'beautiful',
    'awesome', 'epic', 'cool', 'good', 'great', 'amazing', 'best', 'classic',
    'all', 'music', 'albums i own', 'american', 'british', 'german', 'swedish',
    'canadian', 'australian', 'under 2000 listeners', '00s', '90s', '80s', '70s',
    '60s', '2000s', '2010s',
}


def _get_top_tag(artist_name, api_key, min_score):
    """Return (tag_name, score) or None for the best usable Last.fm genre tag."""
    params = urllib.parse.urlencode({
        'method':  'artist.getTopTags',
        'artist':  artist_name,
        'api_key': api_key,
        'format':  'json',
        'autocorrect': 1,
    })
    url = f'{LASTFM_API}?{params}'
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            data = json.loads(r.read())
    except Exception as e:
        return None, str(e)

    if 'error' in data:
        return None, data.get('message', 'lastfm error')

    tags = data.get('toptags', {}).get('tag', [])
    for tag in tags:
        name  = tag.get('name', '').strip()
        score = int(tag.get('count', 0))
        if not name or name.lower() in SKIP_TAGS:
            continue
        if score < min_score:
            break  # tags are sorted descending
        return name, score

    return None, 'no usable tag'


class Command(BaseCommand):
    help = 'Backfill PlaylistTrack genre via Last.fm artist.getTopTags'

    def add_arguments(self, parser):
        parser.add_argument('--artist',    help='Only process this artist name')
        parser.add_argument('--dry-run',   action='store_true', help='Print changes without saving')
        parser.add_argument('--force',     action='store_true', help='Re-tag tracks that already have a genre')
        parser.add_argument('--min-score', type=int, default=30, help='Min Last.fm tag score 0-100 (default 30)')

    def handle(self, *args, **options):
        api_key = getattr(settings, 'LASTFM_API_KEY', '')
        if not api_key:
            self.stderr.write('LASTFM_API_KEY not configured in settings.')
            return

        dry_run   = options['dry_run']
        force     = options['force']
        min_score = options['min_score']
        only      = options.get('artist')

        qs = PlaylistTrack.objects.all()
        if not force:
            qs = qs.filter(genre__isnull=True)
        if only:
            qs = qs.filter(artist_name__iexact=only)

        # Deduplicate by artist_name so we only hit Last.fm once per artist
        artist_names = (
            qs.exclude(artist_name='')
              .values_list('artist_name', flat=True)
              .distinct()
              .order_by('artist_name')
        )

        self.stdout.write(f'Artists to look up: {artist_names.count()}')

        tagged = 0
        skipped = 0

        for name in artist_names:
            tag_name, detail = _get_top_tag(name, api_key, min_score)

            if not tag_name:
                self.stdout.write(f'  SKIP  {name!r} — {detail}')
                skipped += 1
                time.sleep(0.25)
                continue

            self.stdout.write(f'  TAG   {name!r} → {tag_name!r} ({detail})')

            if not dry_run:
                try:
                    genre_obj = Genre.objects.get(name__iexact=tag_name)
                except Genre.DoesNotExist:
                    genre_obj = Genre.objects.create(name=tag_name)

                track_qs = qs.filter(artist_name__iexact=name)
                track_qs.update(genre=genre_obj, genre_raw=tag_name)
                tagged += track_qs.count()

            time.sleep(0.25)  # ~4 req/s, well under Last.fm's 5/s limit

        self.stdout.write(
            self.style.SUCCESS(
                f'\nDone. Artists tagged: {artist_names.count() - skipped}, '
                f'tracks updated: {tagged}, skipped: {skipped}'
                + (' (dry run — no changes saved)' if dry_run else '')
            )
        )
