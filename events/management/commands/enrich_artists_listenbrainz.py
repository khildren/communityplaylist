"""
enrich_artists_listenbrainz — pull artist metadata from ListenBrainz (no API key required).

Requires Artist.mb_id to be set (run enrich_artists_lastfm or enrich_artists_musicbrainz first).

Populates (blank fields only, existing manual data is never overwritten):
  • genre      ← top LB tag (real listen counts, linked to MusicBrainz genre IDs)
  • website    ← 'official homepage' relation
  • instagram  ← extracted from 'social network' relation if it's an Instagram URL
  • youtube    ← 'youtube' relation URL
  • city       ← 'area' field (country / region)

Run:
    python manage.py enrich_artists_listenbrainz
    python manage.py enrich_artists_listenbrainz --artist "Danny Brown"
    python manage.py enrich_artists_listenbrainz --dry-run
    python manage.py enrich_artists_listenbrainz --force-genre
    python manage.py enrich_artists_listenbrainz --all
"""
import re
import time
import logging
import urllib.request
import urllib.parse
import json

from django.core.management.base import BaseCommand
from django.db.models import Exists, OuterRef
from django.utils import timezone

from events.models import Artist, Genre, PlaylistTrack, VideoTrack
from events.management.commands.enrich_tracks_lastfm import SKIP_TAGS

logger = logging.getLogger(__name__)

LB_META_URL = 'https://api.listenbrainz.org/1/metadata/artist/'
LB_UA       = 'CommunityPlaylist/1.0 (andrew.jubinsky@gmail.com)'

_IG_RE = re.compile(r'instagram\.com/([A-Za-z0-9_.]+)', re.I)


def _lb_fetch(mbids):
    """
    Fetch LB metadata for a batch of MBIDs.
    Returns {mbid: row_dict} or {} on error.
    Response is a list; we key it by artist_mbid.
    """
    url = LB_META_URL + '?' + urllib.parse.urlencode({
        'artist_mbids': ','.join(mbids),
        'inc':          'tag',
    })
    try:
        req = urllib.request.Request(url, headers={'User-Agent': LB_UA})
        with urllib.request.urlopen(req, timeout=12) as r:
            rows = json.loads(r.read())
        if isinstance(rows, list):
            return {r['artist_mbid']: r for r in rows if r.get('artist_mbid')}
        return {}
    except Exception as exc:
        logger.debug('ListenBrainz request failed: %s', exc)
        return {}


def _pick_genre(tag_data):
    """Pick highest-count non-skipped artist tag. Returns name or None."""
    tags = tag_data.get('artist', [])
    if isinstance(tags, dict):
        tags = [tags]
    tags = sorted(tags, key=lambda t: int(t.get('count', 0)), reverse=True)
    for t in tags:
        name = (t.get('tag') or '').strip()
        if name and name.lower() not in SKIP_TAGS:
            return name.title()
    return None


def _extract_instagram(rels):
    """Extract Instagram handle from social network URLs in rels dict."""
    for key, url in rels.items():
        if 'social' in key.lower() or 'instagram' in key.lower():
            m = _IG_RE.search(url or '')
            if m:
                return m.group(1)
    return None


class Command(BaseCommand):
    help = 'Enrich Artist profiles from ListenBrainz metadata (requires mb_id)'

    def add_arguments(self, parser):
        parser.add_argument('--artist',      help='Only this artist (case-insensitive)')
        parser.add_argument('--dry-run',     action='store_true')
        parser.add_argument('--force-genre', action='store_true',
                            help='Overwrite Artist.genre even if already set')
        parser.add_argument('--all',         action='store_true',
                            help='Include artists with no local tracks/videos')
        parser.add_argument('--batch',       type=int, default=25,
                            help='MBIDs per API request (max 25, default 25)')

    def handle(self, *args, **options):
        dry_run     = options['dry_run']
        force_genre = options['force_genre']
        only        = options.get('artist')
        all_mode    = options['all']
        batch_size  = min(options['batch'], 25)

        qs = Artist.objects.select_related('genre').filter(mb_id__gt='', enrichment_locked=False)
        if only:
            qs = qs.filter(name__iexact=only)
        elif not all_mode:
            has_audio = Exists(PlaylistTrack.objects.filter(artist=OuterRef('pk')))
            has_video = Exists(VideoTrack.objects.filter(artist=OuterRef('pk'), is_active=True,
                                                          source_type='youtube'))
            qs = qs.filter(has_audio | has_video)

        artists = list(qs.order_by('name'))
        total   = len(artists)
        self.stdout.write(f'Querying ListenBrainz for {total} artist(s) …\n')

        updated = unchanged = not_found = 0

        for batch_start in range(0, total, batch_size):
            batch    = artists[batch_start:batch_start + batch_size]
            mbid_map = {a.mb_id: a for a in batch}
            data     = _lb_fetch(list(mbid_map.keys()))
            time.sleep(0.5)

            for mbid, artist in mbid_map.items():
                row = data.get(mbid)
                if not row:
                    self.stdout.write(f'  MISS  {artist.name!r}')
                    not_found += 1
                    continue

                save_fields = ['last_enriched_at']
                fields      = []
                rels        = row.get('rels') or {}

                # ── Genre ─────────────────────────────────────────────────────
                if force_genre or artist.genre_id is None:
                    genre_name = _pick_genre(row.get('tag') or {})
                    if genre_name:
                        if not dry_run:
                            genre_obj, _ = Genre.objects.get_or_create(name=genre_name)
                            artist.genre = genre_obj
                        save_fields.append('genre')
                        fields.append(f'genre={genre_name}')

                # ── Website ───────────────────────────────────────────────────
                if not artist.website and rels.get('official homepage'):
                    artist.website = rels['official homepage']
                    save_fields.append('website')
                    fields.append('website')

                # ── YouTube ───────────────────────────────────────────────────
                if not artist.youtube and rels.get('youtube'):
                    artist.youtube = rels['youtube']
                    save_fields.append('youtube')
                    fields.append('youtube')

                # ── Instagram ─────────────────────────────────────────────────
                if not artist.instagram:
                    ig = _extract_instagram(rels)
                    if ig:
                        artist.instagram = ig
                        save_fields.append('instagram')
                        fields.append(f'instagram={ig}')

                # ── City / area ───────────────────────────────────────────────
                if not artist.city and row.get('area'):
                    artist.city = row['area']
                    save_fields.append('city')
                    fields.append(f'city={row["area"]}')

                if not dry_run:
                    artist.last_enriched_at = timezone.now()
                    artist.save(update_fields=save_fields)

                if fields:
                    self.stdout.write(f'  OK    {artist.name!r}: {", ".join(fields)}')
                    updated += 1
                else:
                    self.stdout.write(f'  SKIP  {artist.name!r} — nothing new')
                    unchanged += 1

        self.stdout.write(self.style.SUCCESS(
            f'\nDone — {updated} updated, {unchanged} unchanged, {not_found} not in LB'
            + (' (dry run)' if dry_run else '')
        ))
