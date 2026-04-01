"""
management command: python manage.py geocode_events

Geocodes approved events that are missing lat/lng, and reverse-geocodes
neighborhood from coordinates. Safe to re-run — skips events that already
have coordinates.

Run as needed or via cron after bulk imports.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from events.models import Event
from events.geocode import geocode_location, reverse_geocode_neighborhood, NominatimRateLimited
import time


class Command(BaseCommand):
    help = 'Geocode approved events missing coordinates'

    def add_arguments(self, parser):
        parser.add_argument('--all', action='store_true', help='Include pending events too')
        parser.add_argument('--limit', type=int, default=0, help='Max events to process (0=all)')

    def handle(self, *args, **options):
        qs = Event.objects.filter(latitude__isnull=True).exclude(location='')
        # Skip URL-only locations
        qs = qs.exclude(location__startswith='http').exclude(location__startswith='www')

        if not options['all']:
            qs = qs.filter(status='approved')

        # --- Deduplicate: geocode each unique location string once, then
        # bulk-stamp all events that share it. One Nominatim call per venue
        # instead of one per event instance — clears recurring-event backlogs fast.
        unique_locations = list(
            qs.values_list('location', flat=True).distinct()
        )
        if options['limit']:
            unique_locations = unique_locations[:options['limit']]

        self.stdout.write(
            f'Geocoding {len(unique_locations)} unique locations '
            f'(covering {qs.count()} events)...'
        )

        done_locs = failed = events_stamped = hoods = 0
        rate_limited = False

        for i, loc in enumerate(unique_locations):
            try:
                lat, lng = geocode_location(loc)
            except NominatimRateLimited:
                self.stderr.write(f'  Rate limited by Nominatim after {done_locs} locations — stopping early.')
                rate_limited = True
                break

            if lat:
                hood = reverse_geocode_neighborhood(lat, lng)
                matching = qs.filter(location=loc)
                update_fields = {'latitude': lat, 'longitude': lng}
                if hood:
                    matching.filter(neighborhood='').update(neighborhood=hood)
                    hoods += 1
                stamped = matching.update(**update_fields)
                events_stamped += stamped
                done_locs += 1
            else:
                failed += 1

            if (i + 1) % 10 == 0:
                self.stdout.write(f'  {done_locs} locations done, {failed} failed, {events_stamped} events stamped...')

            time.sleep(1)  # Nominatim: max 1 req/sec

        status = 'Stopped early (rate limited)' if rate_limited else 'Done'
        self.stdout.write(self.style.SUCCESS(
            f'{status}. {done_locs} locations geocoded → {events_stamped} events stamped '
            f'({hoods} neighborhoods found), {failed} locations no result.'
        ))
