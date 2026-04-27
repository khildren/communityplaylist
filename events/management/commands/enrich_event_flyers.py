"""
enrich_event_flyers — scan event flyer URLs through local Ollama (moondream)
and pre-fill any missing fields on the Event record.

Scans events that have flyer_url set and flyer_scanned=False.
Only fills fields that are currently blank — never overwrites existing data.

Usage:
    python manage.py enrich_event_flyers
    python manage.py enrich_event_flyers --limit 20
    python manage.py enrich_event_flyers --event-id 123
    python manage.py enrich_event_flyers --dry-run
    python manage.py enrich_event_flyers --rescan     # re-scan already-scanned
"""
import re
from datetime import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from events.models import Event, Artist, Venue
from events.utils.flyer_scan import scan_flyer


class Command(BaseCommand):
    help = 'Scan event flyer URLs with local Ollama moondream and enrich missing fields'

    def add_arguments(self, parser):
        parser.add_argument('--limit',    type=int, default=50)
        parser.add_argument('--event-id', type=int, dest='event_id')
        parser.add_argument('--dry-run',  action='store_true')
        parser.add_argument('--rescan',   action='store_true', help='Re-scan already-scanned events')

    def handle(self, *args, **options):
        dry    = options['dry_run']
        limit  = options['limit']
        rescan = options['rescan']

        qs = Event.objects.filter(flyer_url__gt='')
        if options['event_id']:
            qs = qs.filter(id=options['event_id'])
        elif not rescan:
            qs = qs.filter(flyer_scanned=False)

        qs = qs.order_by('-created_at')[:limit]
        total = qs.count()
        self.stdout.write(f'Scanning {total} events (dry={dry}, rescan={rescan})\n')

        enriched = skipped = failed = 0

        for ev in qs:
            self.stdout.write(f'  [{ev.id}] {ev.title[:50]} — {ev.flyer_url[:60]}')
            result = scan_flyer(ev.flyer_url)

            if not result:
                self.stdout.write(self.style.WARNING('      → scan returned empty'))
                failed += 1
                if not dry:
                    ev.flyer_scanned = True
                    ev.save(update_fields=['flyer_scanned'])
                continue

            self.stdout.write(f'      → {list(result.keys())}')
            changed = []

            if not dry:
                # Fill missing fields — never overwrite existing values
                if result.get('title') and not ev.title:
                    ev.title = result['title'][:200]
                    changed.append('title')

                if result.get('description') and not ev.description:
                    ev.description = result['description']
                    changed.append('description')

                if result.get('venue_name') and not ev.location:
                    loc = result['venue_name']
                    if result.get('venue_address'):
                        loc = f"{loc}, {result['venue_address']}"
                    ev.location = loc[:300]
                    changed.append('location')

                if result.get('date') and not ev.start_date:
                    try:
                        time_str = result.get('start_time') or result.get('doors_time') or '20:00'
                        dt = datetime.strptime(f"{result['date']} {time_str}", '%Y-%m-%d %H:%M')
                        ev.start_date = timezone.make_aware(dt)
                        changed.append('start_date')
                    except ValueError:
                        pass

                if result.get('price'):
                    price_raw = result['price'].lower()
                    if not ev.price_info:
                        ev.price_info = result['price'][:100]
                        changed.append('price_info')
                    if 'free' in price_raw and ev.is_free is True:
                        ev.is_free = True
                    elif re.search(r'\d', price_raw):
                        ev.is_free = False
                        changed.append('is_free')

                if result.get('ticket_url') and not ev.website:
                    ev.website = result['ticket_url'][:500]
                    changed.append('website')

                # Link artist stubs if names match existing Artists
                if result.get('artists'):
                    for name in result['artists'][:10]:
                        artist = Artist.objects.filter(
                            name__iexact=name.strip(), is_stub=False
                        ).first()
                        if artist and artist not in ev.artists.all():
                            ev.artists.add(artist)
                            changed.append(f'artist:{artist.name}')

                ev.flyer_scanned = True
                fields = ['flyer_scanned']
                for f in ['title', 'description', 'location', 'start_date', 'price_info', 'is_free', 'website']:
                    if f in changed:
                        fields.append(f)
                ev.save(update_fields=fields)

            if changed:
                self.stdout.write(self.style.SUCCESS(f'      ✓ filled: {", ".join(changed)}'))
                enriched += 1
            else:
                self.stdout.write('      → no new fields to fill')
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(f'\nDone — enriched={enriched} skipped={skipped} failed={failed}')
        )
