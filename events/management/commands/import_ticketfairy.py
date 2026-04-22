"""
management command: python manage.py import_ticketfairy

Pulls upcoming Portland-area events from TicketFairy via their public
Algolia search index (key is embedded in their own JS, read-only).

Two passes:
  1. Geo search — 50 km radius around Portland, OR
  2. Text search — query "portland" to catch events tagged to the city by name

Run nightly via cron.
"""
import re, time, pytz, requests
from datetime import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify
from events.models import Event

PDX_TZ    = pytz.timezone('America/Los_Angeles')
PDX_LAT   = 45.5051
PDX_LNG   = -122.6750
RADIUS_M  = 130_000         # coast to Mt. Hood (~80 mi)
MAX_GEO   = 100
MAX_TEXT  = 50
BASE_URL  = 'https://www.ticketfairy.com'

# Public Algolia credentials embedded in TicketFairy's own search JS
ALGOLIA_APP = '674YM1N5RW'
ALGOLIA_KEY = '26898fb11bd6ec3aa906a33fc751ae2d'
ALGOLIA_URL = f'https://{ALGOLIA_APP.lower()}-2.algolianet.com/1/indexes/*/queries'
INDEX_ASC   = 'home_page_events_www_theticketfairy_com_date_asc'


def _algolia(requests_list):
    r = requests.post(
        ALGOLIA_URL,
        headers={
            'X-Algolia-API-Key':        ALGOLIA_KEY,
            'X-Algolia-Application-Id': ALGOLIA_APP,
        },
        json={'requests': requests_list},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _collect(now_ts):
    """Return deduplicated list of raw Algolia hit dicts for Portland area."""
    hits_by_id = {}

    # Pass 1: geo search
    try:
        data = _algolia([{
            'indexName': INDEX_ASC,
            'params': (
                f'hitsPerPage={MAX_GEO}'
                f'&aroundLatLng={PDX_LAT},{PDX_LNG}'
                f'&aroundRadius={RADIUS_M}'
                f'&numericFilters=["timestamp>{now_ts}"]'
            ),
        }])
        for h in data['results'][0].get('hits', []):
            hits_by_id[h['objectID']] = h
    except Exception:
        pass

    # Pass 2: text search for "portland"
    try:
        data = _algolia([{
            'indexName': INDEX_ASC,
            'params': (
                f'hitsPerPage={MAX_TEXT}'
                f'&query=portland'
                f'&numericFilters=["timestamp>{now_ts}"]'
            ),
        }])
        for h in data['results'][0].get('hits', []):
            hits_by_id[h['objectID']] = h
    except Exception:
        pass

    return list(hits_by_id.values())


class Command(BaseCommand):
    help = 'Import upcoming Portland-area events from TicketFairy'

    def handle(self, *a, **k):
        now     = timezone.now()
        now_ts  = int(now.timestamp())
        created = skipped = errors = 0

        hits = _collect(now_ts)
        self.stdout.write(f'TicketFairy: {len(hits)} raw hits')

        for h in hits:
            try:
                title = (h.get('title') or '').strip()
                if not title:
                    skipped += 1
                    continue

                # Skip online-only events
                if h.get('is_online_event'):
                    skipped += 1
                    continue

                ts_start = h.get('timestamp')
                ts_end   = h.get('end_timestamp')
                if not ts_start:
                    skipped += 1
                    continue

                dtstart = PDX_TZ.normalize(
                    pytz.utc.localize(datetime.utcfromtimestamp(ts_start)).astimezone(PDX_TZ)
                )
                dtend = PDX_TZ.normalize(
                    pytz.utc.localize(datetime.utcfromtimestamp(ts_end)).astimezone(PDX_TZ)
                ) if ts_end else None

                # Build full event URL
                raw_url  = h.get('url', '')
                ev_url   = (BASE_URL + raw_url) if raw_url.startswith('/') else raw_url

                # Dedup: website URL first, then slug+date
                existing = None
                if ev_url:
                    existing = Event.objects.filter(website=ev_url[:200]).first()
                if not existing:
                    slug_base = slugify(f"{title}-{dtstart.strftime('%Y-%m-%d')}")
                    existing  = Event.objects.filter(slug__startswith=slug_base).first()
                if existing:
                    skipped += 1
                    continue

                # Location: prefer venue + city, fall back to just city
                venue_name = (h.get('venue_name') or '').strip()
                city       = (h.get('event_city') or 'Portland').strip()
                location   = f"{venue_name}, {city}" if venue_name else city

                description = (h.get('short_description') or '').strip()[:2000]
                if ev_url:
                    description = (description + f'\n\nTickets: {ev_url}').strip()[:2000]

                # Unique slug
                slug_base = slugify(f"{title}-{dtstart.strftime('%Y-%m-%d')}")
                slug      = slug_base
                n         = 1
                while Event.objects.filter(slug=slug).exists():
                    slug = f'{slug_base}-{n}'; n += 1

                Event.objects.create(
                    title       = title[:200],
                    slug        = slug,
                    description = description or f'Event at {location}',
                    location    = location[:300],
                    start_date  = dtstart,
                    end_date    = dtend,
                    website     = ev_url[:200],
                    category    = 'music',
                    is_free     = False,
                    status      = 'pending',
                    submitted_by= 'ticketfairy-import',
                    latitude    = h.get('_geoloc', {}).get('lat'),
                    longitude   = h.get('_geoloc', {}).get('lng'),
                )
                created += 1

            except Exception as e:
                errors += 1
                self.stderr.write(f'  error on {h.get("title","?")}: {e}')

        self.stdout.write(
            f'TicketFairy done — created: {created}, skipped: {skipped}, errors: {errors}'
        )
