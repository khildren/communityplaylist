"""
enrich_artists_youtube — find artist YouTube channels via the YouTube Data API v3.

Populates: youtube URL, youtube_channel_id, and pulls channel description
           as a bio supplement if artist has no bio yet.

Run:
    python manage.py enrich_artists_youtube
    python manage.py enrich_artists_youtube --stubs-only
    python manage.py enrich_artists_youtube --name "Gnosis"
    python manage.py enrich_artists_youtube --dry-run
"""
import time
import urllib.request
import urllib.parse
import json
from django.core.management.base import BaseCommand

YT_SEARCH_URL = 'https://www.googleapis.com/youtube/v3/search'
YT_CHANNEL_URL = 'https://www.googleapis.com/youtube/v3/channels'


def _yt_search_channel(name, api_key):
    """Search YouTube for a music artist channel. Returns best-match channel dict or None."""
    params = urllib.parse.urlencode({
        'part': 'snippet',
        'q': f'{name} music artist',
        'type': 'channel',
        'maxResults': 5,
        'key': api_key,
    })
    req = urllib.request.Request(f'{YT_SEARCH_URL}?{params}')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
    except Exception:
        return None

    items = data.get('items', [])
    if not items:
        return None

    lower = name.lower()
    for item in items:
        title = item['snippet']['channelTitle'].lower()
        if title == lower or title.startswith(lower):
            return {
                'channel_id':    item['snippet']['channelId'],
                'channel_title': item['snippet']['channelTitle'],
                'description':   item['snippet']['description'],
            }
    return None


def _yt_channel_details(channel_id, api_key):
    """Get subscriber count and full description for a channel."""
    params = urllib.parse.urlencode({
        'part': 'snippet,statistics',
        'id': channel_id,
        'key': api_key,
    })
    req = urllib.request.Request(f'{YT_CHANNEL_URL}?{params}')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
    except Exception:
        return {}

    items = data.get('items', [])
    if not items:
        return {}
    item = items[0]
    return {
        'description':   item['snippet'].get('description', ''),
        'subscribers':   item['statistics'].get('subscriberCount', ''),
        'view_count':    item['statistics'].get('viewCount', ''),
        'thumbnail':     item['snippet'].get('thumbnails', {}).get('high', {}).get('url', ''),
    }


class Command(BaseCommand):
    help = 'Enrich Artist stubs with YouTube channel data.'

    def add_arguments(self, parser):
        parser.add_argument('--stubs-only', action='store_true',
                            help='Only process is_stub=True artists')
        parser.add_argument('--force', action='store_true',
                            help='Re-fetch even if artist already has a YouTube URL')
        parser.add_argument('--name', type=str, default='',
                            help='Enrich a single artist by name')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print results without saving')

    def handle(self, *args, **options):
        from django.conf import settings
        from events.models import Artist
        from django.utils import timezone

        api_key = getattr(settings, 'YOUTUBE_API_KEY', '')
        if not api_key:
            self.stderr.write('YOUTUBE_API_KEY not set in settings.')
            return

        stubs_only  = options['stubs_only']
        force       = options['force']
        dry_run     = options['dry_run']
        name_filter = options['name'].strip()

        qs = Artist.objects.all()
        if name_filter:
            qs = qs.filter(name__iexact=name_filter)
        elif stubs_only:
            qs = qs.filter(is_stub=True)
        if not force:
            qs = qs.filter(youtube='')

        total = qs.count()
        self.stdout.write(f'Searching YouTube channels for {total} artists…')

        found = skipped = errors = 0

        for artist in qs:
            try:
                match = _yt_search_channel(artist.name, api_key)
            except Exception as e:
                self.stderr.write(f'  ERROR {artist.name}: {e}')
                errors += 1
                time.sleep(1)
                continue

            if not match:
                self.stdout.write(f'  — not found: {artist.name}')
                skipped += 1
                time.sleep(0.1)
                continue

            channel_id = match['channel_id']
            details    = _yt_channel_details(channel_id, api_key)
            subs       = details.get('subscribers', '?')
            yt_url     = f'https://www.youtube.com/channel/{channel_id}'

            self.stdout.write(
                f'  ✓ {artist.name} → "{match["channel_title"]}" | '
                f'subs={subs} | {yt_url}'
            )

            if not dry_run:
                update_fields = ['last_enriched_at']
                if not artist.youtube:
                    artist.youtube = yt_url
                    update_fields.append('youtube')
                if not artist.youtube_channel_id:
                    artist.youtube_channel_id = channel_id
                    update_fields.append('youtube_channel_id')
                # Supplement auto_bio with channel description if meaningful
                desc = details.get('description', '').strip()
                if desc and len(desc) > 50 and not artist.bio:
                    if 'YouTube:' not in artist.auto_bio:
                        artist.auto_bio = artist.auto_bio.rstrip() + f'\n\nFrom YouTube: {desc[:300]}'
                        update_fields.append('auto_bio')
                artist.last_enriched_at = timezone.now()
                artist.save(update_fields=update_fields)

            found += 1
            time.sleep(0.2)

        self.stdout.write(
            f'\nDone. {found} enriched, {skipped} not found, {errors} errors.'
        )
