"""
backfill_artist_links — parse artist names from approved music event titles,
create Artist stubs for new names, and link them via M2M.

Strategy: two-pass.
  Pass 1: count how many events each parsed name appears in.
  Pass 2: only create/link names that appear in ≥min_appearances events
          (default 2, matching the stub threshold).

Run:
    python manage.py backfill_artist_links
    python manage.py backfill_artist_links --dry-run
    python manage.py backfill_artist_links --min-appearances 2
"""
import re
from collections import Counter, defaultdict
from django.core.management.base import BaseCommand

# ── Name quality filters ──────────────────────────────────────────────────────

# Generic English words that bleed through "with X" parsing
SKIP_EXACT = {
    'pdx', 'portland', 'oregon', 'or', 'presents', 'live', 'session',
    'night', 'morning', 'evening', 'day', 'room', 'stage', 'floor',
    'more', 'tba', 'tbd', 'special', 'guests', 'guest', 'various',
    'artists', 'artist', 'local', 'djs', 'bands', 'open', 'mic',
    'the', 'a', 'an', 'and', 'with', 'feat', 'vs', 'b2b',
    'beer', 'wine', 'yoga', 'food', 'music', 'dance', 'comedy', 'party',
    'show', 'event', 'fest', 'festival', 'night', 'tour', 'release',
    'friends', 'family', 'friends', 'community', 'happy', 'hour',
}

# Words that indicate an event-type name, not an artist
EVENT_TYPE_RE = re.compile(
    r'\b(club|swap|run|tasting|trivia|bingo|karaoke|chess|game night|game|'
    r'tattoo|craft|scribe|meditation|adoption|party|show|nights?|dance night|'
    r'wednesday|monday|tuesday|thursday|friday|saturday|sunday|'
    r'weekly|monthly|annual|season|series|presents)\b',
    re.IGNORECASE,
)

# Names that are plainly generic single words
GENERIC_SINGLES = {'girls', 'gays', 'theys', 'they', 'queer', 'special', 'guests', 'guest'}
SKIP_PHRASES   = {'special guests', 'special guest', 'and friends', 'various artists'}

# Name must start with a capital letter (rules out "a Cheese Tasting" bleed-through)
STARTS_CAPITAL_RE = re.compile(r'^[A-Z]')


def _is_plausible_artist(name):
    if not name or len(name) < 2:
        return False
    lower = name.lower()
    if lower in SKIP_EXACT:
        return False
    if lower in GENERIC_SINGLES:
        return False
    if lower in SKIP_PHRASES:
        return False
    if re.match(r'^\d+$', name):
        return False
    if len(name) > 50:
        return False
    # Must start with a capital letter
    if not STARTS_CAPITAL_RE.match(name):
        return False
    # Parentheses → door times / notes bled into name, e.g. "Artist (8pm Doors)"
    if '(' in name or ')' in name:
        return False
    # Contains event-type keywords → not an artist
    if EVENT_TYPE_RE.search(name):
        return False
    return True


class Command(BaseCommand):
    help = 'Parse artist names from music event titles and create/link Artist stubs.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would be created without writing.',
        )
        parser.add_argument(
            '--categories', default='music,hybrid',
            help='Comma-separated event categories to process (default: music,hybrid)',
        )
        parser.add_argument(
            '--min-appearances', type=int, default=2,
            help='Name must appear in at least this many events to be created (default: 2)',
        )

    def handle(self, *args, **options):
        from events.models import Artist, Event
        from events.views import _parse_lineup_from_title

        dry_run         = options['dry_run']
        categories      = [c.strip() for c in options['categories'].split(',')]
        min_appearances = options['min_appearances']

        events = (
            Event.objects
            .filter(status='approved', category__in=categories)
            .prefetch_related('artists')
            .order_by('start_date')
        )
        total = events.count()
        self.stdout.write(f'Pass 1: scanning {total} events in {categories}…')

        # Pass 1: count appearances per name, store event PKs per name
        name_events = defaultdict(set)   # name → set of event PKs
        event_names = defaultdict(list)  # event PK → list of plausible names

        for event in events:
            parsed = _parse_lineup_from_title(event.title)
            # Include crew-classified names that are actually solo DJ artists
            candidates = parsed.get('artists', []) + [
                n for n in parsed.get('crews', [])
                if re.match(r'^DJ\s+\S', n, re.IGNORECASE)
            ]
            for raw in candidates:
                name = raw.strip().strip('.').strip(',')
                if _is_plausible_artist(name):
                    name_events[name].add(event.pk)
                    event_names[event.pk].append(name)

        # Filter to names meeting the threshold
        qualified = {name: pks for name, pks in name_events.items()
                     if len(pks) >= min_appearances}

        self.stdout.write(
            f'Pass 1 done. {len(name_events)} unique names found, '
            f'{len(qualified)} qualify (≥{min_appearances} events).\n'
        )
        self.stdout.write('── Qualified names ──────────────────────────────')
        for name, pks in sorted(qualified.items(), key=lambda x: -len(x[1])):
            artist = Artist.objects.filter(name__iexact=name).first()
            status = '✓ exists' if artist else '+ new'
            self.stdout.write(f'  {len(pks):3d}×  [{status}]  {name}')

        if dry_run:
            self.stdout.write(f'\n[DRY RUN] No changes written.')
            return

        # Pass 2: create artists and link to events
        self.stdout.write('\nPass 2: creating artists and linking events…')
        artists_created = 0
        links_created   = 0
        links_skipped   = 0

        for name, event_pks in qualified.items():
            artist = Artist.objects.filter(name__iexact=name).first()
            if not artist:
                artist = Artist(name=name)
                artist.save()
                artists_created += 1
                self.stdout.write(self.style.SUCCESS(f'  Created: "{name}"'))

            for ev in Event.objects.filter(pk__in=event_pks).prefetch_related('artists'):
                if not ev.artists.filter(pk=artist.pk).exists():
                    ev.artists.add(artist)
                    links_created += 1
                else:
                    links_skipped += 1

        self.stdout.write(
            f'\nDone. {artists_created} artists created, '
            f'{links_created} event links added, {links_skipped} already linked.'
        )
