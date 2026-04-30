"""
discover_local_artists — find new Portland-area artists from MusicBrainz.

MusicBrainz's area field records where an artist is based or from, so querying
area:"Portland" reliably returns Portland OR artists (2 600+ in the index).

New artists are created as stubs (is_stub=True) so auto_stub_artists and the
enrichment pipeline can flesh them out. Existing artists (matched by name or
mb_id) are never duplicated; artists found in MB but already in the DB only
get their mb_id back-filled if it was missing.

Run:
    python manage.py discover_local_artists
    python manage.py discover_local_artists --dry-run
    python manage.py discover_local_artists --area "Oregon"   # broaden to state
    python manage.py discover_local_artists --limit 500       # cap total MB results
"""
import time
import json
import urllib.request
import urllib.parse
import logging

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify

logger = logging.getLogger(__name__)

MB_BASE    = 'https://musicbrainz.org/ws/2'
MB_HEADERS = {'User-Agent': 'CommunityPlaylist/1.0 (andrew.jubinsky@gmail.com)'}

# Artist types we want — skip orchestras, choirs, "other", etc.
MB_WANTED_TYPES = {'person', 'group', 'duo', 'character', ''}


def _mb_area_search(area_name, limit=100, offset=0):
    """
    Query MusicBrainz for artists whose area matches area_name.
    Returns (artists_list, total_count).
    """
    params = urllib.parse.urlencode({
        'query':  f'area:"{area_name}"',
        'fmt':    'json',
        'limit':  limit,
        'offset': offset,
    })
    req = urllib.request.Request(f'{MB_BASE}/artist?{params}', headers=MB_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        return data.get('artists', []), int(data.get('count', 0))
    except Exception as exc:
        logger.warning('MusicBrainz search error (offset=%d): %s', offset, exc)
        return [], 0


class Command(BaseCommand):
    help = 'Discover Portland-area artists from MusicBrainz, create stub profiles'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--area',    default='Portland',
                            help='MusicBrainz area name to search (default: "Portland")')
        parser.add_argument('--limit',   type=int, default=400,
                            help='Max MB results to process (default: 400)')
        parser.add_argument('--city',    default='Portland, OR',
                            help='City label to save on new stubs (default: "Portland, OR")')

    def handle(self, *args, **options):
        from events.models import Artist

        dry_run    = options['dry_run']
        area_name  = options['area']
        max_fetch  = options['limit']
        city_label = options['city']

        # Build lookup maps of existing artists
        existing_names = set(Artist.objects.values_list('name', flat=True))
        existing_mbids = set(
            Artist.objects.exclude(mb_id='').values_list('mb_id', flat=True)
        )
        # name → pk for mb_id back-fills
        name_to_pk = dict(Artist.objects.values_list('name', 'pk'))

        self.stdout.write(f'Querying MusicBrainz for area="{area_name}" artists …')

        candidates    = {}  # name → {mb_id, is_update}
        offset        = 0
        page_size     = 100
        fetched       = 0
        total_known   = None

        while fetched < max_fetch:
            rows, total = _mb_area_search(area_name, limit=page_size, offset=offset)
            if total_known is None:
                total_known = total
                self.stdout.write(f'  MusicBrainz reports {total:,} "{area_name}" artists')

            if not rows:
                break

            for a in rows:
                atype = (a.get('type') or '').lower()
                if atype not in MB_WANTED_TYPES:
                    continue
                name = (a.get('name') or '').strip()
                mbid = (a.get('id')   or '').strip()
                if not name:
                    continue

                # Already in DB by mb_id — skip entirely
                if mbid and mbid in existing_mbids:
                    continue

                # Already in DB by name — maybe back-fill mb_id
                if name in existing_names:
                    if mbid:
                        candidates[name] = {'mb_id': mbid, 'is_update': True}
                    continue

                # Genuinely new
                candidates[name] = {'mb_id': mbid, 'is_update': False}

            fetched += len(rows)
            offset  += page_size

            if total_known and fetched >= min(total_known, max_fetch):
                break
            time.sleep(1.1)  # MB rate limit: 1 req/sec

        new_count    = sum(1 for v in candidates.values() if not v['is_update'])
        update_count = sum(1 for v in candidates.values() if v['is_update'])
        self.stdout.write(f'  {new_count} new artists, {update_count} existing need mb_id fill')

        created   = 0
        mb_filled = 0

        for name, info in sorted(candidates.items()):
            if info['is_update']:
                # Back-fill mb_id on existing artist
                if not dry_run:
                    pk = name_to_pk.get(name)
                    if pk:
                        Artist.objects.filter(pk=pk, mb_id='').update(mb_id=info['mb_id'])
                        mb_filled += 1
                        self.stdout.write(f'  FILL  {name!r} → mb_id={info["mb_id"]}')
                else:
                    self.stdout.write(f'  [DRY] FILL  {name!r} → mb_id={info["mb_id"]}')
                continue

            auto_bio = (
                f'{name} is a Portland-area artist discovered via MusicBrainz. '
                f'This stub was auto-created — is this you? '
                f'Claim it to add your bio, links, and music.'
            )

            if dry_run:
                self.stdout.write(
                    f'  [DRY] NEW  {name!r}  mb_id={info["mb_id"] or "—"}'
                )
                created += 1
                continue

            try:
                artist = Artist(
                    name     = name,
                    mb_id    = info['mb_id'],
                    city     = city_label,
                    is_stub  = True,
                    auto_bio = auto_bio,
                    last_enriched_at = timezone.now(),
                )
                artist.save()
                self.stdout.write(f'  NEW   {name!r}')
                created += 1
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f'  SKIP  {name!r}: {exc}'))

        suffix = ' (dry run)' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(
            f'\nDone — {created} stubs created, {mb_filled} mb_id fills{suffix}'
        ))
