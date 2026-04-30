"""
enrich_tracks_lastfm — backfill genre on PlaylistTrack records using Last.fm.

Lookup priority per track:
  1. track.getInfo(artist, title)  → toptags from the specific recording
  2. artist.getTopTags(artist)     → top tag for the artist (cached per artist name)

For crew/venue tracks (no artist_name, linked via promoter/venue FK):
  Scans the track title for known artist names and uses those for the lookup.

Run:
    python manage.py enrich_tracks_lastfm
    python manage.py enrich_tracks_lastfm --artist "Binsky"
    python manage.py enrich_tracks_lastfm --dry-run
    python manage.py enrich_tracks_lastfm --force      # re-tag even if genre already set
    python manage.py enrich_tracks_lastfm --min-score 30
    python manage.py enrich_tracks_lastfm --track-only  # skip artist fallback
"""
import time
import urllib.request
import urllib.parse
import json
import logging

from django.core.management.base import BaseCommand
from django.conf import settings
from events.models import PlaylistTrack, Genre, Artist

logger = logging.getLogger(__name__)
LASTFM_API = 'https://ws.audioscrobbler.com/2.0/'

# Tags to skip — too generic or not music genres
SKIP_TAGS = {
    'seen live', 'favourite', 'favorites', 'favourites', 'love', 'beautiful',
    'awesome', 'epic', 'cool', 'good', 'great', 'amazing', 'best', 'classic',
    'all', 'music', 'albums i own', 'under 2000 listeners', '00s', '90s', '80s', '70s',
    '60s', '2000s', '2010s',
    # nationality / language tags — not genres
    'american', 'british', 'german', 'swedish', 'canadian', 'australian',
    'japanese', 'korean', 'french', 'norwegian', 'finnish', 'icelandic',
    'j-pop', 'k-pop',
}


def _lastfm_get(params, api_key):
    """Make a Last.fm API GET, return parsed JSON or None."""
    params['api_key'] = api_key
    params['format']  = 'json'
    url = f'{LASTFM_API}?' + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            data = json.loads(r.read())
        if 'error' in data:
            return None
        return data
    except Exception as exc:
        logger.debug('Last.fm request failed: %s', exc)
        return None


def _pick_tag(tags, min_score=0):
    """Return (tag_name, score) from a Last.fm tags list, or (None, None)."""
    if isinstance(tags, dict):
        tags = [tags]   # single tag returned as object, not list
    for tag in tags:
        name  = (tag.get('name') or '').strip()
        score = int(tag.get('count', 0) or 0)
        if not name or name.lower() in SKIP_TAGS:
            continue
        if score and score < min_score:
            break   # sorted descending; no point continuing
        return name, score
    return None, None


def get_track_genre(artist_name, track_title, api_key, min_score):
    """
    Try track.getInfo first (most specific).
    Returns (genre_name, source) or (None, None).
    Track tags have no score field — any non-skipped tag is accepted.
    """
    data = _lastfm_get({
        'method':      'track.getInfo',
        'artist':      artist_name,
        'track':       track_title,
        'autocorrect': 1,
    }, api_key)
    if not data:
        return None, None
    tags = data.get('track', {}).get('toptags', {}).get('tag', [])
    name, _ = _pick_tag(tags)
    if name:
        return name, 'track'
    return None, None


def get_artist_genre(artist_name, api_key, min_score):
    """
    Fallback: artist.getTopTags.
    Returns (genre_name, score) or (None, None).
    """
    data = _lastfm_get({
        'method':      'artist.getTopTags',
        'artist':      artist_name,
        'autocorrect': 1,
    }, api_key)
    if not data:
        return None, None
    tags = data.get('toptags', {}).get('tag', [])
    return _pick_tag(tags, min_score)


def _find_artist_in_title(title, artist_index):
    """
    Scan a track title for any known artist name.
    artist_index is {name_lower: display_name}.
    Returns display_name or None.
    Only matches names longer than 3 chars to avoid noise.
    """
    title_lower = title.lower()
    best = None
    best_len = 0
    for name_lower, display in artist_index.items():
        if len(name_lower) > 3 and name_lower in title_lower:
            if len(name_lower) > best_len:
                best, best_len = display, len(name_lower)
    return best


class Command(BaseCommand):
    help = 'Backfill PlaylistTrack genre via Last.fm (track.getInfo → artist.getTopTags)'

    def add_arguments(self, parser):
        parser.add_argument('--artist',     help='Only process this artist name')
        parser.add_argument('--dry-run',    action='store_true', help='Print changes without saving')
        parser.add_argument('--force',      action='store_true', help='Re-tag tracks that already have a genre')
        parser.add_argument('--min-score',  type=int, default=30, help='Min Last.fm tag score 0-100 (default 30)')
        parser.add_argument('--track-only', action='store_true', help='Skip artist fallback, track.getInfo only')

    def handle(self, *args, **options):
        api_key   = getattr(settings, 'LASTFM_API_KEY', '')
        if not api_key:
            self.stderr.write('LASTFM_API_KEY not configured in settings.')
            return

        dry_run    = options['dry_run']
        force      = options['force']
        min_score  = options['min_score']
        only       = options.get('artist')
        track_only = options['track_only']

        qs = PlaylistTrack.objects.select_related('genre', 'artist', 'promoter', 'venue')
        if not force:
            qs = qs.filter(genre__isnull=True)
        if only:
            qs = qs.filter(artist_name__iexact=only)

        # Build known artist name index for crew/venue title scanning
        artist_index = {
            a.lower(): a
            for a in Artist.objects.values_list('name', flat=True)
            if len(a) > 3
        }

        # Cache artist-level lookups (expensive, avoid repeating per track)
        artist_cache = {}  # name_lower → genre_name or None

        tagged = skipped = 0

        for track in qs.select_related('genre', 'artist', 'promoter', 'venue'):
            artist_name = track.artist_name.strip()

            # For crew/venue tracks with no artist_name: scan title for artist names
            scan_artist = None
            if not artist_name and (track.promoter_id or track.venue_id):
                scan_artist = _find_artist_in_title(track.title, artist_index)
                if scan_artist:
                    self.stdout.write(
                        f'  SCAN  {track.title[:50]!r} → artist in title: {scan_artist!r}'
                    )

            lookup_name = artist_name or scan_artist
            if not lookup_name:
                self.stdout.write(f'  SKIP  pk={track.pk} — no artist name')
                skipped += 1
                continue

            genre_name = None

            # ── Step 1: track.getInfo ──────────────────────────────────────
            genre_name, src = get_track_genre(lookup_name, track.title, api_key, min_score)
            if genre_name:
                self.stdout.write(
                    f'  TRACK {lookup_name!r} / {track.title[:40]!r} → {genre_name!r}'
                )
            time.sleep(0.2)

            # ── Step 2: artist.getTopTags (fallback, cached) ──────────────
            if not genre_name and not track_only:
                key = lookup_name.lower()
                if key not in artist_cache:
                    g, score = get_artist_genre(lookup_name, api_key, min_score)
                    artist_cache[key] = g
                    if g:
                        self.stdout.write(
                            f'  ARTST {lookup_name!r} → {g!r} ({score})'
                        )
                    else:
                        self.stdout.write(f'  SKIP  {lookup_name!r} — no usable tag')
                    time.sleep(0.2)
                genre_name = artist_cache[key]

            if not genre_name:
                skipped += 1
                continue

            if not dry_run:
                try:
                    genre_obj = Genre.objects.get(name__iexact=genre_name)
                except Genre.DoesNotExist:
                    genre_obj = Genre.objects.create(name=genre_name)
                track.genre     = genre_obj
                track.genre_raw = genre_name
                track.save(update_fields=['genre', 'genre_raw'])

            tagged += 1

        self.stdout.write(self.style.SUCCESS(
            f'\nDone — {tagged} tagged, {skipped} skipped'
            + (' (dry run)' if dry_run else '')
        ))
