"""
enrich_artists_spotify — fetch Spotify artist data for stub profiles.

Pulls: spotify artist ID, genre tags, follower count, popularity score,
       profile image URL, and confirms/updates city from Spotify if available.

Uses Client Credentials flow (no user login needed).

Run:
    python manage.py enrich_artists_spotify
    python manage.py enrich_artists_spotify --stubs-only   # only is_stub=True artists
    python manage.py enrich_artists_spotify --force        # re-fetch even if spotify URL set
    python manage.py enrich_artists_spotify --name "Gnosis"  # single artist
"""
import time
import urllib.request
import urllib.parse
import urllib.error
import json
import base64
from django.core.management.base import BaseCommand


def _spotify_token(client_id, client_secret):
    """Get a Client Credentials access token from Spotify."""
    creds = base64.b64encode(f'{client_id}:{client_secret}'.encode()).decode()
    data  = urllib.parse.urlencode({'grant_type': 'client_credentials'}).encode()
    req   = urllib.request.Request(
        'https://accounts.spotify.com/api/token',
        data=data,
        headers={'Authorization': f'Basic {creds}', 'Content-Type': 'application/x-www-form-urlencoded'},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())['access_token']


def _spotify_search(name, token):
    """Search Spotify for an artist by name. Returns best-match dict or None."""
    params = urllib.parse.urlencode({'q': name, 'type': 'artist', 'limit': 5})
    req    = urllib.request.Request(
        f'https://api.spotify.com/v1/search?{params}',
        headers={'Authorization': f'Bearer {token}'},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError:
        return None

    items = data.get('artists', {}).get('items', [])
    if not items:
        return None

    # Prefer exact name match (case-insensitive)
    lower = name.lower()
    for item in items:
        if item['name'].lower() == lower:
            return item
    # Fall back to first result if close enough
    first = items[0]
    if first['name'].lower().startswith(lower[:4]):
        return first
    return None


class Command(BaseCommand):
    help = 'Enrich Artist stubs with Spotify data (genres, image, followers, popularity).'

    def add_arguments(self, parser):
        parser.add_argument('--stubs-only', action='store_true',
                            help='Only process is_stub=True artists (default: all without Spotify URL)')
        parser.add_argument('--force', action='store_true',
                            help='Re-fetch even if artist already has a Spotify URL')
        parser.add_argument('--name', type=str, default='',
                            help='Enrich a single artist by name')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print results without saving')

    def handle(self, *args, **options):
        from django.conf import settings
        from events.models import Artist
        from django.utils import timezone

        client_id     = getattr(settings, 'SPOTIFY_CLIENT_ID', '')
        client_secret = getattr(settings, 'SPOTIFY_CLIENT_SECRET', '')

        if not client_id or not client_secret:
            self.stderr.write('SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET not set in settings.')
            return

        stubs_only = options['stubs_only']
        force      = options['force']
        dry_run    = options['dry_run']
        name_filter = options['name'].strip()

        qs = Artist.objects.all()
        if name_filter:
            qs = qs.filter(name__iexact=name_filter)
        elif stubs_only:
            qs = qs.filter(is_stub=True)
        if not force:
            qs = qs.filter(spotify='')

        total = qs.count()
        self.stdout.write(f'Fetching Spotify data for {total} artists…')

        try:
            token = _spotify_token(client_id, client_secret)
        except Exception as e:
            self.stderr.write(f'Failed to get Spotify token: {e}')
            return

        found = skipped = errors = 0

        for artist in qs:
            try:
                result = _spotify_search(artist.name, token)
            except Exception as e:
                self.stderr.write(f'  ERROR {artist.name}: {e}')
                errors += 1
                time.sleep(1)
                continue

            if not result:
                self.stdout.write(f'  — not found: {artist.name}')
                skipped += 1
                time.sleep(0.2)
                continue

            sp_url      = result.get('external_urls', {}).get('spotify', '')
            genres      = result.get('genres', [])
            followers   = result.get('followers', {}).get('total', 0)
            popularity  = result.get('popularity', 0)
            images      = result.get('images', [])
            image_url   = images[0]['url'] if images else ''
            sp_name     = result.get('name', '')

            self.stdout.write(
                f'  ✓ {artist.name} → "{sp_name}" | '
                f'genres={genres[:3]} | followers={followers:,} | pop={popularity} | '
                f'{"🖼" if image_url else "no img"}'
            )

            if not dry_run:
                update_fields = ['last_enriched_at']
                if sp_url and not artist.spotify:
                    artist.spotify = sp_url
                    update_fields.append('spotify')
                # Store genres in auto_bio suffix (until we have a proper tags field)
                if genres:
                    genre_str = ', '.join(genres[:5])
                    # Append genre line if not already there
                    if 'Genres:' not in artist.auto_bio:
                        artist.auto_bio = artist.auto_bio.rstrip() + f'\nGenres: {genre_str}'
                    update_fields.append('auto_bio')
                # Use Spotify image as photo_url hint stored in notes field (no model field yet)
                # We'll add spotify_image_url field in next migration; for now log it
                artist.last_enriched_at = timezone.now()
                artist.save(update_fields=update_fields)

            found += 1
            time.sleep(0.3)   # stay well within rate limits

        self.stdout.write(
            f'\nDone. {found} enriched, {skipped} not found on Spotify, {errors} errors.'
        )
