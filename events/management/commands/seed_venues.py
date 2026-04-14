"""
seed_venues — auto-create Venue stubs from event location data and VenueFeeds.

Two passes:
  1. Event locations — group approved events by location string, create a Venue
     stub for any location that appears 2+ times (geocoded ones preferred).
  2. VenueFeed records — any active feed that looks like a real venue (has a
     website, is not an aggregator) gets a linked Venue profile.

All created venues are unverified (verified=False) so they stay off the public
listing until an admin approves them via /admin/events/venue/.

Run:
    python manage.py seed_venues
    python manage.py seed_venues --min-events 3   # stricter threshold
    python manage.py seed_venues --dry-run        # preview only
"""

import re
from django.core.management.base import BaseCommand
from django.db.models import Count, Avg
from events.models import Event, Venue, VenueFeed


# VenueFeeds whose names indicate they're aggregators, not real venues
AGGREGATOR_KEYWORDS = [
    'calagator', 'musicbrainz', 'meetup', 'eventbrite', 'puzzled pint',
    'auto-discovered', 'pdx python', 'pdx tech', 'subduction',
    'a bar in portland', 'solve the puzzle',
]

# Minimum address length — skip bare city names like "Portland, OR"
MIN_ADDRESS_LEN = 15


def looks_like_aggregator(name):
    nl = name.lower()
    return any(kw in nl for kw in AGGREGATOR_KEYWORDS)


def parse_location(loc):
    """
    Split a location string into (venue_name, address).

    "Starday Tavern, 6517 SE Foster Rd, Portland, OR 97206, USA"
      → ("Starday Tavern", "6517 SE Foster Rd, Portland, OR 97206")

    "310 NW Glisan St, Portland, OR 97209"
      → ("", "310 NW Glisan St, Portland, OR 97209")
    """
    loc = loc.strip().rstrip(', ')
    parts = [p.strip() for p in loc.split(',')]
    if not parts:
        return '', loc

    first = parts[0]

    # If first token starts with a digit it's a street number → pure address
    if first and first[0].isdigit():
        # Strip trailing country name
        address = ', '.join(p for p in parts if p.upper() not in ('USA', 'US', 'UNITED STATES', 'UNITED STATES OF AMERICA'))
        return '', address.strip().strip(',')

    # First token looks like a venue name — everything after is address
    name = first
    address_parts = parts[1:]
    address = ', '.join(p for p in address_parts if p.upper() not in ('USA', 'US', 'UNITED STATES', 'UNITED STATES OF AMERICA'))
    return name.strip(), address.strip().strip(',')


def _norm(s):
    """Lowercase, strip punctuation/spaces for fuzzy comparison."""
    return re.sub(r'[^a-z0-9]', '', s.lower())


def _keywords(s):
    """Significant words (5+ chars) from a string, for overlap scoring."""
    words = re.sub(r'[^a-z0-9 ]', ' ', s.lower()).split()
    skip = {'street', 'avenue', 'portland', 'oregon', 'united', 'states', 'suite', 'floor', 'north', 'south', 'lounge', 'publi'}
    return {w for w in words if len(w) >= 5 and w not in skip}


def already_covered(name, address, existing_venues):
    """True if an existing Venue already matches this name/address."""
    name_n    = _norm(name)
    address_n = _norm(address)
    name_kw   = _keywords(name)

    for v in existing_venues:
        v_name_n    = _norm(v.name)
        v_address_n = _norm(v.address)
        v_name_kw   = _keywords(v.name)

        # Address substring match (handles trailing USA, spacing diffs)
        if address_n and len(address_n) > 8:
            if address_n in v_address_n or v_address_n in address_n:
                return True

        # Normalised name exact match
        if name_n and len(name_n) > 3 and name_n == v_name_n:
            return True

        # Substring match on normalised name
        if name_n and len(name_n) > 5:
            if name_n in v_name_n or v_name_n in name_n:
                return True

        # Keyword overlap: if 2+ significant words match, treat as same venue
        if name_kw and v_name_kw:
            overlap = name_kw & v_name_kw
            if len(overlap) >= 2:
                return True
            # Single very-distinctive keyword (rare long word) also counts
            if len(overlap) == 1 and len(next(iter(overlap))) >= 8:
                return True

    return False


class Command(BaseCommand):
    help = 'Seed Venue stubs from event location data and VenueFeed records'

    def add_arguments(self, parser):
        parser.add_argument('--min-events', type=int, default=2,
                            help='Minimum number of events at a location to create a venue stub')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print what would be created without saving anything')

    def handle(self, *args, **options):
        dry   = options['dry_run']
        min_n = options['min_events']
        tag   = '[DRY RUN] ' if dry else ''

        created = 0
        skipped = 0

        existing_venues = list(Venue.objects.all())

        # ── Pass 1: Event locations ──────────────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING('\n── Pass 1: Event locations ──'))

        rows = (
            Event.objects
            .filter(status='approved', latitude__isnull=False)
            .exclude(location__iregex=r'^https?://')
            .exclude(location__iregex=r'^www\.')
            .values('location', 'neighborhood')
            .annotate(n=Count('id'), lat=Avg('latitude'), lng=Avg('longitude'))
            .order_by('-n')
        )

        for row in rows:
            loc  = row['location']
            n    = row['n']
            lat  = row['lat']
            lng  = row['lng']
            hood = row['neighborhood'] or ''

            if n < min_n:
                continue
            if len(loc) < MIN_ADDRESS_LEN:
                skipped += 1
                self.stdout.write(f'  SKIP (too short): {loc!r}')
                continue

            vname, address = parse_location(loc)

            if not address and not vname:
                skipped += 1
                continue

            # Use the full loc as address if we couldn't extract one
            if not address:
                address = loc

            if already_covered(vname, address, existing_venues):
                skipped += 1
                self.stdout.write(f'  SKIP (exists): {vname or address!r}')
                continue

            display_name = vname or address[:60]
            self.stdout.write(
                f'  {tag}CREATE venue: {display_name!r}  '
                f'({n} events, {hood})'
            )

            if not dry:
                v = Venue(
                    name=display_name,
                    address=address,
                    neighborhood=hood,
                    latitude=lat,
                    longitude=lng,
                    verified=False,
                    active=True,
                )
                v.save()
                existing_venues.append(v)
                created += 1

        # ── Pass 2: VenueFeed records ────────────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING('\n── Pass 2: VenueFeeds ──'))

        for vf in VenueFeed.objects.filter(active=True):
            if looks_like_aggregator(vf.name):
                self.stdout.write(f'  SKIP (aggregator): {vf.name!r}')
                continue

            # Skip if already linked
            if hasattr(vf, 'venue_profile') and vf.venue_profile:
                self.stdout.write(f'  SKIP (linked): {vf.name!r}')
                continue

            if already_covered(vf.name, '', existing_venues):
                self.stdout.write(f'  SKIP (exists): {vf.name!r}')
                continue

            self.stdout.write(f'  {tag}CREATE venue from feed: {vf.name!r}')

            if not dry:
                v = Venue(
                    name=vf.name,
                    address='',
                    website=vf.website or '',
                    venue_feed=vf,
                    verified=False,
                    active=True,
                )
                v.save()
                existing_venues.append(v)
                created += 1

        # ── Summary ──────────────────────────────────────────────────────────
        self.stdout.write('')
        if dry:
            self.stdout.write(self.style.WARNING(f'Dry run — nothing saved.'))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'Done. Created {created} venue stubs, skipped {skipped}. '
                f'Review and verify at /admin/events/venue/'
            ))
