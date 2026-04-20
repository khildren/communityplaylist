"""
auto_stub_artists — scan approved events for artist names appearing in ≥N shows,
create/update stub Artist profiles with geo data and auto-generated bios.

Run:
    python manage.py auto_stub_artists
    python manage.py auto_stub_artists --min-shows 3 --dry-run
    python manage.py auto_stub_artists --force-refresh  # re-process already-stubbed artists
"""
from collections import Counter
from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone


class Command(BaseCommand):
    help = 'Auto-create/update stub Artist profiles for names appearing in ≥N approved events.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--min-shows', type=int, default=2,
            help='Minimum approved events to qualify for a stub (default: 2)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print what would happen without writing to the database.',
        )
        parser.add_argument(
            '--force-refresh', action='store_true',
            help='Re-run geo/bio enrichment even on already-stubbed artists.',
        )

    def handle(self, *args, **options):
        from events.models import Artist, Event

        min_shows    = options['min_shows']
        dry_run      = options['dry_run']
        force        = options['force_refresh']

        # Artists with ≥min_shows approved events
        qualifying = (
            Event.objects
            .filter(status='approved')
            .values('artists__id', 'artists__name')
            .annotate(show_count=Count('id', distinct=True))
            .filter(show_count__gte=min_shows)
            .exclude(artists__isnull=True)
            .order_by('-show_count')
        )

        created = updated = skipped = 0

        for row in qualifying:
            artist_id   = row['artists__id']
            artist_name = row['artists__name']
            show_count  = row['show_count']

            if not artist_id or not artist_name:
                continue

            try:
                artist = Artist.objects.get(pk=artist_id)
            except Artist.DoesNotExist:
                continue

            # Skip claimed artists — they manage their own profiles
            if artist.claimed_by_id:
                skipped += 1
                continue

            has_real_profile = any([artist.bio, artist.website, artist.instagram,
                                    artist.soundcloud, artist.bandcamp, artist.photo])

            # Skip artists with real profiles unless --force-refresh
            if has_real_profile and not force:
                skipped += 1
                continue

            # ── Geo derivation from event venue cluster ────────────────────────
            events_qs = Event.objects.filter(
                status='approved', artists__id=artist_id
            )

            lats, lngs, neighborhoods, venue_names = [], [], [], []

            for ev in events_qs:
                if ev.latitude and ev.longitude:
                    lats.append(ev.latitude)
                    lngs.append(ev.longitude)

                if ev.neighborhood:
                    neighborhoods.append(ev.neighborhood.strip())

                # Parse city from free-text location (e.g. "Venue, Portland, OR 97201")
                loc = ev.location or ''
                if 'portland' in loc.lower():
                    pass   # city defaults to Portland below

                # Use first part of location as venue name
                vname = loc.split(',')[0].strip() if loc else ''
                if vname:
                    venue_names.append(vname)

            avg_lat = sum(lats) / len(lats) if lats else None
            avg_lng = sum(lngs) / len(lngs) if lngs else None
            home_hood = Counter(neighborhoods).most_common(1)[0][0] if neighborhoods else ''
            home_city = 'Portland, OR'

            # ── Auto-bio generation ────────────────────────────────────────────
            top_venues = [v for v, _ in Counter(venue_names).most_common(3)]
            venue_str  = ', '.join(top_venues) if top_venues else 'local venues'
            hood_str   = f' in the {home_hood}' if home_hood else ''

            auto_bio = (
                f'{artist_name} has performed at {show_count} events on CommunityPlaylist'
                f'{hood_str}, with appearances at {venue_str}. '
                f'This profile was auto-generated from event history — '
                f'is this you? Claim it to add your bio, links, and music.'
            )

            if dry_run:
                self.stdout.write(
                    f'[DRY RUN] {"stub" if artist.is_stub else "NEW stub"}: '
                    f'"{artist_name}" ({show_count} shows) | '
                    f'hood={home_hood or "?"} lat={f"{avg_lat:.4f}" if avg_lat else "?"}'
                )
                created += 1
                continue

            # ── Write to DB ────────────────────────────────────────────────────
            changed = []
            if not artist.is_stub:
                artist.is_stub = True
                changed.append('is_stub')
            if avg_lat and not artist.latitude:
                artist.latitude  = avg_lat
                artist.longitude = avg_lng
                changed.append('geo')
            if home_hood and not artist.home_neighborhood:
                artist.home_neighborhood = home_hood
                changed.append('neighborhood')
            if home_city and not artist.city:
                artist.city = home_city
                changed.append('city')
            # Always refresh auto_bio (it's cheap and reflects current event count)
            artist.auto_bio = auto_bio
            changed.append('auto_bio')
            artist.last_enriched_at = timezone.now()

            # Ensure slug exists
            artist.save()

            if 'is_stub' in changed and 'geo' not in changed and avg_lat is None:
                label = 'stub (no geo)'
            else:
                label = 'stub+geo' if avg_lat else 'stub'

            self.stdout.write(self.style.SUCCESS(
                f'[{label}] "{artist_name}" ({show_count} shows) → /artists/{artist.slug}/ '
                f'[{", ".join(changed)}]'
            ))
            if artist.is_stub and 'is_stub' not in changed:
                updated += 1
            else:
                created += 1

        verb = 'would process' if dry_run else 'processed'
        self.stdout.write(
            f'\nDone. {created} stubs {verb}, {updated} refreshed, {skipped} skipped.'
        )
