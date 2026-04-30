"""
enrich_artists_lastfm — build Artist profiles from Last.fm artist.getInfo.

Populates per artist (all fields are opt-in — existing manual data is never overwritten):
  • auto_bio       ← Last.fm bio summary (HTML-stripped), only if artist.bio is blank
  • genre          ← top non-skipped tag, only if Artist.genre is null
  • mb_id          ← MusicBrainz UUID from Last.fm, only if Artist.mb_id is blank
  • lastfm_listeners ← listener count (always refreshed)
  • lastfm_similar   ← list of similar artist names (always refreshed)
  • last_enriched_at ← timestamp

Run:
    python manage.py enrich_artists_lastfm
    python manage.py enrich_artists_lastfm --artist "Danny Brown"
    python manage.py enrich_artists_lastfm --dry-run
    python manage.py enrich_artists_lastfm --force-bio   # overwrite auto_bio even if set
    python manage.py enrich_artists_lastfm --force-genre # overwrite Artist.genre even if set
    python manage.py enrich_artists_lastfm --all         # include artists with no video/playlist tracks
"""
import re
import time
import logging
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db.models import Exists, OuterRef
from django.utils import timezone

from events.models import Artist, Genre, PlaylistTrack, VideoTrack
from events.management.commands.enrich_tracks_lastfm import _lastfm_get, _pick_tag

logger = logging.getLogger(__name__)

_HTML_RE = re.compile(r'<[^>]+>')
_LINK_RE = re.compile(r'<a\s[^>]*>.*?</a>', re.I | re.S)


def _clean_bio(raw):
    """Strip HTML and the trailing Last.fm 'Read more on Last.fm' link."""
    text = _LINK_RE.sub('', raw or '')
    text = _HTML_RE.sub('', text)
    text = re.sub(r'\s+', ' ', text).strip()
    # Trim trailing 'User-contributed text …' boilerplate
    cut = text.find('User-contributed text is available')
    if cut > 0:
        text = text[:cut].strip()
    return text


def get_artist_info(name, api_key):
    """
    Call artist.getInfo and return a dict:
      {bio, mbid, listeners, playcount, tags, similar}
    or None on failure.
    """
    data = _lastfm_get({
        'method':      'artist.getInfo',
        'artist':      name,
        'autocorrect': 1,
        'lang':        'en',
    }, api_key)
    if not data or 'artist' not in data:
        return None
    a = data['artist']

    bio_raw = (a.get('bio') or {}).get('content', '')
    bio     = _clean_bio(bio_raw)

    stats     = a.get('stats') or {}
    listeners = int(stats.get('listeners') or 0)

    mbid = (a.get('mbid') or '').strip()

    tags_raw = (a.get('tags') or {}).get('tag', [])
    if isinstance(tags_raw, dict):
        tags_raw = [tags_raw]

    similar_raw = (a.get('similar') or {}).get('artist', [])
    if isinstance(similar_raw, dict):
        similar_raw = [similar_raw]
    similar = [s['name'] for s in similar_raw if s.get('name')][:10]

    return {
        'bio':       bio,
        'mbid':      mbid,
        'listeners': listeners,
        'tags':      tags_raw,
        'similar':   similar,
    }


class Command(BaseCommand):
    help = 'Enrich Artist profiles from Last.fm artist.getInfo (bio, genre, listeners, similar)'

    def add_arguments(self, parser):
        parser.add_argument('--artist',      help='Only process this artist (case-insensitive)')
        parser.add_argument('--dry-run',     action='store_true')
        parser.add_argument('--force-bio',   action='store_true',
                            help='Overwrite auto_bio even if already set')
        parser.add_argument('--all',         action='store_true',
                            help='Include artists with no local tracks/videos (default: local-only)')

    def handle(self, *args, **options):
        api_key    = getattr(settings, 'LASTFM_API_KEY', '')
        if not api_key:
            self.stderr.write('LASTFM_API_KEY not in settings.')
            return

        dry_run   = options['dry_run']
        force_bio = options['force_bio']
        only      = options.get('artist')
        all_mode  = options['all']

        qs = Artist.objects.select_related('genre').filter(enrichment_locked=False)
        if only:
            qs = qs.filter(name__iexact=only)
        elif not all_mode:
            has_audio = Exists(PlaylistTrack.objects.filter(artist=OuterRef('pk')))
            has_video = Exists(VideoTrack.objects.filter(artist=OuterRef('pk'), is_active=True,
                                                          source_type='youtube'))
            qs = qs.filter(has_audio | has_video)

        qs = qs.order_by('name')
        total = qs.count()
        self.stdout.write(f'Enriching {total} artist(s) via Last.fm …\n')

        updated = skipped = failed = 0

        for artist in qs:
            info = get_artist_info(artist.name, api_key)
            time.sleep(0.3)

            if not info:
                self.stdout.write(f'  MISS  {artist.name!r}')
                failed += 1
                continue

            save_fields = ['last_enriched_at']
            fields = []

            # ── Bio ───────────────────────────────────────────────────────────
            if info['bio'] and (force_bio or not artist.auto_bio):
                artist.auto_bio = info['bio']
                save_fields.append('auto_bio')
                fields.append('bio')

            # ── MusicBrainz ID ────────────────────────────────────────────────
            if info['mbid'] and not artist.mb_id:
                artist.mb_id = info['mbid']
                save_fields.append('mb_id')
                fields.append('mb_id')

            # Genre is handled by enrich_artist_genres_lastfm (uses getTopTags with scores)
            # getInfo tags have no count field — skip here to avoid overwriting curated data

            # ── Listeners + similar (always refresh) ─────────────────────────
            if info['listeners']:
                artist.lastfm_listeners = info['listeners']
                save_fields.append('lastfm_listeners')
                fields.append(f'listeners={info["listeners"]:,}')

            if info['similar']:
                artist.lastfm_similar = info['similar']
                save_fields.append('lastfm_similar')
                fields.append(f'similar={len(info["similar"])}')

            if not dry_run:
                artist.last_enriched_at = timezone.now()
                artist.save(update_fields=save_fields)

            if fields:
                self.stdout.write(f'  OK    {artist.name!r}: {", ".join(fields)}')
                updated += 1
            else:
                self.stdout.write(f'  SKIP  {artist.name!r} — nothing new')
                skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f'\nDone — {updated} updated, {skipped} unchanged, {failed} not found'
            + (' (dry run)' if dry_run else '')
        ))
