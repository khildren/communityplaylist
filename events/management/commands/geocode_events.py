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
from events.geocode import geocode_location, reverse_geocode_neighborhood
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

        if options['limit']:
            qs = qs[:options['limit']]

        events = list(qs)
        self.stdout.write(f'Geocoding {len(events)} events...')

        done = failed = hoods = 0
        for i, ev in enumerate(events):
            lat, lng = geocode_location(ev.location)
            if lat:
                ev.latitude = lat
                ev.longitude = lng
                hood = reverse_geocode_neighborhood(lat, lng)
                if hood and not ev.neighborhood:
                    ev.neighborhood = hood
                    hoods += 1
                ev.save(update_fields=['latitude', 'longitude', 'neighborhood'])
                done += 1
            else:
                failed += 1

            if (i + 1) % 25 == 0:
                self.stdout.write(f'  {done} geocoded, {failed} failed...')

            time.sleep(1)  # Nominatim: max 1 req/sec

        self.stdout.write(self.style.SUCCESS(
            f'Done. {done} geocoded ({hoods} neighborhoods found), {failed} no result.'
        ))
