"""
enrich_artists_discogs — enrich Artist records via the free Discogs database API.

Discogs is the most complete electronic/DJ artist database with a free public API.

Pulls: discogs URL, bio supplement (profile text), SoundCloud/Bandcamp/Instagram/
       Beatport links from the artist's URL list.

No API key required (read-only, 60 req/min limit unauthenticated).

Run:
    python manage.py enrich_artists_discogs
    python manage.py enrich_artists_discogs --stubs-only
    python manage.py enrich_artists_discogs --name "Sullivan King"
    python manage.py enrich_artists_discogs --dry-run
    python manage.py enrich_artists_discogs --force
"""
import time
import urllib.request
import urllib.parse
import json
from django.core.management.base import BaseCommand

DISCOGS_API = 'https://api.discogs.com'
DISCOGS_HEADERS = {'User-Agent': 'CommunityPlaylist/1.0 +communityplaylist.com'}


def _discogs_search(name):
    """Search Discogs for an artist. Returns best match dict or None."""
    params = urllib.parse.urlencode({'q': name, 'type': 'artist', 'per_page': 10})
    req = urllib.request.Request(
        f'{DISCOGS_API}/database/search?{params}', headers=DISCOGS_HEADERS
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except Exception:
        return None

    results = data.get('results', [])
    if not results:
        return None

    lower = name.lower()
    exact = [r for r in results if r.get('title', '').lower() == lower]
    return exact[0] if exact else results[0]


def _discogs_artist_detail(artist_id):
    """Fetch full Discogs artist record (profile text + URLs)."""
    req = urllib.request.Request(
        f'{DISCOGS_API}/artists/{artist_id}', headers=DISCOGS_HEADERS
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def _extract_links(urls):
    """Parse Discogs URL list into a dict of platform → URL/handle."""
    links = {}
    for url in urls:
        url = url.strip()
        if 'soundcloud.com/' in url and 'soundcloud' not in links:
            links['soundcloud'] = url.split('soundcloud.com/')[-1].strip('/')
        elif 'bandcamp.com' in url and 'bandcamp' not in links:
            links['bandcamp'] = url
        elif 'instagram.com/' in url and 'instagram' not in links:
            links['instagram'] = url.split('instagram.com/')[-1].strip('/')
        elif 'beatport.com/artist/' in url and 'beatport' not in links:
            links['beatport'] = url
        elif 'beatport.com/label/' in url and 'beatport_label' not in links:
            links['beatport_label'] = url
        elif ('youtube.com/' in url or 'youtu.be' in url) and 'youtube' not in links:
            links['youtube'] = url
        elif 'spotify.com/artist/' in url and 'spotify' not in links:
            links['spotify'] = url
        elif 'mixcloud.com/' in url and 'mixcloud' not in links:
            links['mixcloud'] = url.split('mixcloud.com/')[-1].strip('/')
    return links


class Command(BaseCommand):
    help = 'Enrich Artist stubs using the free Discogs database API.'

    def add_arguments(self, parser):
        parser.add_argument('--stubs-only', action='store_true',
                            help='Only process is_stub=True artists')
        parser.add_argument('--force', action='store_true',
                            help='Re-fetch even if artist already has a Discogs URL')
        parser.add_argument('--name', type=str, default='',
                            help='Enrich a single artist by name')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print results without saving')

    def handle(self, *args, **options):
        from events.models import Artist
        from django.utils import timezone

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
            qs = qs.filter(discogs='')

        total = qs.count()
        self.stdout.write(f'Searching Discogs for {total} artists…')

        found = skipped = errors = 0

        for artist in qs:
            time.sleep(1.1)  # stay under 60 req/min unauthenticated

            try:
                result = _discogs_search(artist.name)
            except Exception as e:
                self.stderr.write(f'  ERROR {artist.name}: {e}')
                errors += 1
                continue

            if not result:
                self.stdout.write(f'  — not found: {artist.name}')
                skipped += 1
                continue

            discogs_id  = result.get('id')
            discogs_uri = result.get('uri', '')
            discogs_url = f'https://www.discogs.com{discogs_uri}' if discogs_uri else ''

            # Fetch detail for profile + URLs
            time.sleep(1.1)
            detail = _discogs_artist_detail(discogs_id) if discogs_id else {}
            profile = detail.get('profile', '').strip()
            urls    = detail.get('urls', [])
            links   = _extract_links(urls)

            self.stdout.write(
                f'  ✓ {artist.name} | {discogs_url} | '
                f'links={list(links.keys())} | bio={bool(profile)}'
            )

            if dry_run:
                found += 1
                continue

            update_fields = ['last_enriched_at']

            if discogs_url and not artist.discogs:
                artist.discogs = discogs_url
                update_fields.append('discogs')

            # Supplement auto_bio with Discogs profile if meaningful and no bio yet
            if profile and len(profile) > 30 and not artist.bio:
                snippet = profile[:400]
                if 'Discogs:' not in artist.auto_bio:
                    artist.auto_bio = (artist.auto_bio.rstrip() + f'\n\nFrom Discogs: {snippet}').lstrip()
                    update_fields.append('auto_bio')

            # Fill platform links that are still empty
            if links.get('soundcloud') and not artist.soundcloud:
                artist.soundcloud = links['soundcloud']
                update_fields.append('soundcloud')
            if links.get('bandcamp') and not artist.bandcamp:
                artist.bandcamp = links['bandcamp']
                update_fields.append('bandcamp')
            if links.get('instagram') and not artist.instagram:
                artist.instagram = links['instagram']
                update_fields.append('instagram')
            if links.get('beatport') and not artist.beatport:
                artist.beatport = links['beatport']
                update_fields.append('beatport')
            if links.get('youtube') and not artist.youtube:
                artist.youtube = links['youtube']
                update_fields.append('youtube')
            if links.get('spotify') and not artist.spotify:
                artist.spotify = links['spotify']
                update_fields.append('spotify')
            if links.get('mixcloud') and not artist.mixcloud:
                artist.mixcloud = links['mixcloud']
                update_fields.append('mixcloud')

            artist.last_enriched_at = timezone.now()
            artist.save(update_fields=update_fields)
            found += 1

        self.stdout.write(
            f'\nDone. {found} enriched, {skipped} not found on Discogs, {errors} errors.'
        )
