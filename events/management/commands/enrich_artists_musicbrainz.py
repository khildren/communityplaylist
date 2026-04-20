"""
enrich_artists_musicbrainz — enrich Artist stubs using the free MusicBrainz API.

Pulls: mb_id, bio (from Wikipedia via MusicBrainz), official website,
       SoundCloud/Bandcamp/Spotify/Instagram links from MB URL relations,
       and area (geo) for city confirmation.

No API key required. Rate-limited to 1 req/sec per MB guidelines.

Run:
    python manage.py enrich_artists_musicbrainz
    python manage.py enrich_artists_musicbrainz --stubs-only
    python manage.py enrich_artists_musicbrainz --name "Gnosis"
    python manage.py enrich_artists_musicbrainz --dry-run
"""
import time
import urllib.request
import urllib.parse
import json
from django.core.management.base import BaseCommand

MB_BASE = 'https://musicbrainz.org/ws/2'
MB_HEADERS = {'User-Agent': 'CommunityPlaylist/1.0 (communityplaylist.com)'}


def _mb_search(name):
    """Search MusicBrainz for an artist. Returns best-match dict or None."""
    params = urllib.parse.urlencode({'query': f'artist:"{name}"', 'fmt': 'json', 'limit': 5})
    req    = urllib.request.Request(f'{MB_BASE}/artist?{params}', headers=MB_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except Exception:
        return None

    artists = data.get('artists', [])
    if not artists:
        return None

    lower = name.lower()
    # Exact name match only — don't auto-accept a high-score match for common/short names
    # that could be a different artist (e.g. "Gnosis" = Portland DnB crew ≠ Norwegian band)
    exact = [a for a in artists if a.get('name', '').lower() == lower]
    if not exact:
        return None
    if len(exact) == 1:
        return exact[0]
    # Multiple exact matches — prefer US/English-speaking country
    for a in exact:
        area = (a.get('area') or a.get('begin-area') or {}).get('name', '')
        if any(c in area for c in ('United States', 'Canada', 'Australia', 'UK')):
            return a
    return exact[0]


def _mb_artist_detail(mbid):
    """Fetch full artist record including URL relations."""
    params = urllib.parse.urlencode({'inc': 'url-rels', 'fmt': 'json'})
    req    = urllib.request.Request(f'{MB_BASE}/artist/{mbid}?{params}', headers=MB_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def _extract_links(relations):
    """Parse MB URL relations into a dict of platform → URL."""
    links = {}
    for rel in relations:
        url    = rel.get('url', {}).get('resource', '')
        rtype  = rel.get('type', '').lower()

        if 'soundcloud' in url:
            links['soundcloud'] = url.split('soundcloud.com/')[-1].strip('/')
        elif 'bandcamp' in url:
            links['bandcamp'] = url
        elif 'spotify' in url and 'artist' in url:
            links['spotify'] = url
        elif 'instagram' in url:
            links['instagram'] = url.split('instagram.com/')[-1].strip('/')
        elif 'youtube' in url or 'youtu.be' in url:
            links['youtube'] = url
        elif rtype == 'official homepage':
            links['website'] = url
        elif 'discogs' in url:
            links['discogs'] = url

    return links


class Command(BaseCommand):
    help = 'Enrich Artist stubs using the free MusicBrainz API.'

    def add_arguments(self, parser):
        parser.add_argument('--stubs-only', action='store_true',
                            help='Only process is_stub=True artists')
        parser.add_argument('--force', action='store_true',
                            help='Re-fetch even if artist already has an mb_id')
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
            qs = qs.filter(mb_id='')

        total = qs.count()
        self.stdout.write(f'Searching MusicBrainz for {total} artists…')

        found = skipped = errors = 0

        for artist in qs:
            time.sleep(1.1)   # MB rate limit: 1 req/sec

            try:
                match = _mb_search(artist.name)
            except Exception as e:
                self.stderr.write(f'  ERROR {artist.name}: {e}')
                errors += 1
                continue

            if not match:
                self.stdout.write(f'  — not found: {artist.name}')
                skipped += 1
                continue

            mbid  = match.get('id', '')
            area  = (match.get('area') or match.get('begin-area') or {}).get('name', '')
            score = match.get('score', 0)

            # Fetch URL relations
            time.sleep(1.1)
            detail = _mb_artist_detail(mbid) if mbid else {}
            links  = _extract_links(detail.get('relations', []))

            self.stdout.write(
                f'  ✓ {artist.name} (score={score}) | mbid={mbid} | '
                f'area={area} | links={list(links.keys())}'
            )

            if dry_run:
                found += 1
                continue

            update_fields = ['last_enriched_at']
            if mbid and not artist.mb_id:
                artist.mb_id = mbid
                update_fields.append('mb_id')
            if area and not artist.city:
                artist.city = area
                update_fields.append('city')
            if links.get('soundcloud') and not artist.soundcloud:
                artist.soundcloud = links['soundcloud']
                update_fields.append('soundcloud')
            if links.get('bandcamp') and not artist.bandcamp:
                artist.bandcamp = links['bandcamp']
                update_fields.append('bandcamp')
            if links.get('spotify') and not artist.spotify:
                artist.spotify = links['spotify']
                update_fields.append('spotify')
            if links.get('instagram') and not artist.instagram:
                artist.instagram = links['instagram']
                update_fields.append('instagram')
            if links.get('youtube') and not artist.youtube:
                artist.youtube = links['youtube']
                update_fields.append('youtube')
            if links.get('website') and not artist.website:
                artist.website = links['website']
                update_fields.append('website')

            artist.last_enriched_at = timezone.now()
            artist.save(update_fields=update_fields)
            found += 1

        self.stdout.write(
            f'\nDone. {found} enriched, {skipped} not found on MusicBrainz, {errors} errors.'
        )
