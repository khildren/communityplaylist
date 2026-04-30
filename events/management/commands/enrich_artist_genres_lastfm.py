"""
enrich_artist_genres_lastfm — set Artist.genre via Last.fm artist.getTopTags.

Targets artists that have no genre set (or --force to re-tag all).
Prioritises artists who have active YouTube VideoTracks so the player
queue pills are populated first.

Run:
    python manage.py enrich_artist_genres_lastfm
    python manage.py enrich_artist_genres_lastfm --artist "Danny Brown"
    python manage.py enrich_artist_genres_lastfm --dry-run
    python manage.py enrich_artist_genres_lastfm --force
    python manage.py enrich_artist_genres_lastfm --min-score 20
    python manage.py enrich_artist_genres_lastfm --all        # include artists without videos
"""
import time
import logging

from django.core.management.base import BaseCommand
from django.conf import settings
from django.db.models import Exists, OuterRef

from events.models import Artist, Genre, VideoTrack
from events.management.commands.enrich_tracks_lastfm import (
    _lastfm_get, _pick_tag, SKIP_TAGS,
)

logger = logging.getLogger(__name__)


def get_artist_genre(artist_name, api_key, min_score):
    data = _lastfm_get({
        'method':      'artist.getTopTags',
        'artist':      artist_name,
        'autocorrect': 1,
    }, api_key)
    if not data:
        return None, None
    tags = data.get('toptags', {}).get('tag', [])
    return _pick_tag(tags, min_score)


class Command(BaseCommand):
    help = 'Enrich Artist.genre via Last.fm artist.getTopTags'

    def add_arguments(self, parser):
        parser.add_argument('--artist',    help='Only process this artist name (case-insensitive)')
        parser.add_argument('--dry-run',   action='store_true', help='Print without saving')
        parser.add_argument('--force',     action='store_true', help='Re-tag artists that already have a genre')
        parser.add_argument('--min-score', type=int, default=25,
                            help='Min Last.fm tag score 0-100 (default 25)')
        parser.add_argument('--all',       action='store_true',
                            help='Include artists with no active YouTube videos (default: videos-only)')

    def handle(self, *args, **options):
        api_key   = getattr(settings, 'LASTFM_API_KEY', '')
        if not api_key:
            self.stderr.write('LASTFM_API_KEY not configured in settings.')
            return

        dry_run   = options['dry_run']
        force     = options['force']
        min_score = options['min_score']
        only      = options.get('artist')
        all_mode  = options['all']

        qs = Artist.objects.select_related('genre').filter(enrichment_locked=False)
        if not force:
            qs = qs.filter(genre__isnull=True)
        if only:
            qs = qs.filter(name__iexact=only)
        if not all_mode and not only:
            # Limit to artists that have at least one active YouTube VideoTrack
            qs = qs.annotate(
                has_video=Exists(
                    VideoTrack.objects.filter(
                        artist=OuterRef('pk'), is_active=True, source_type='youtube'
                    )
                )
            ).filter(has_video=True)

        qs = qs.order_by('name')

        # Skip artists whose name already appears in a PlaylistTrack with a curated genre
        # (those are manually tagged by artist_name text field — respect that, don't overwrite)
        if not force and not only:
            from events.models import PlaylistTrack
            named_genres = set(
                PlaylistTrack.objects.filter(genre__isnull=False, artist_name__gt='')
                .values_list('artist_name', flat=True).distinct()
            )
            # lowercase map for case-insensitive comparison
            named_genres_lower = {n.lower() for n in named_genres}
            skip_ids = [
                a.id for a in qs
                if a.name.lower() in named_genres_lower
            ]
            if skip_ids:
                self.stdout.write(f'Skipping {len(skip_ids)} artist(s) with curated PlaylistTrack genre')
                qs = qs.exclude(pk__in=skip_ids)

        total = qs.count()
        self.stdout.write(f'Processing {total} artist(s) …\n')

        tagged = skipped = 0

        for artist in qs:
            genre_name, score = get_artist_genre(artist.name, api_key, min_score)
            time.sleep(0.25)

            if not genre_name:
                self.stdout.write(f'  SKIP  {artist.name!r} — no usable tag')
                skipped += 1
                continue

            self.stdout.write(f'  TAG   {artist.name!r} → {genre_name!r} ({score})')

            if not dry_run:
                genre_obj, _ = Genre.objects.get_or_create(name=genre_name)
                artist.genre = genre_obj
                artist.save(update_fields=['genre'])

            tagged += 1

        self.stdout.write(self.style.SUCCESS(
            f'\nDone — {tagged} tagged, {skipped} skipped'
            + (' (dry run)' if dry_run else '')
        ))
