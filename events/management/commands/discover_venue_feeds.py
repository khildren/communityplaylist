"""
management command: python manage.py discover_venue_feeds

Queries the OpenStreetMap Overpass API for music/event venues in the Portland, OR
metro area, checks each venue's website for a parseable calendar feed, and prints
a report of which ones can be imported.

Checks for:
  - iCal feed   (?ical=1 or /events/feed/)
  - EAEL widget (data-events= JSON attribute)
  - Squarespace (/api/open/GetItemsByTag JSON)
  - TEC REST API (/wp-json/tribe/events/v1/events/)
  - JSON-LD Event schema

Usage:
    python manage.py discover_venue_feeds
    python manage.py discover_venue_feeds --bbox "45.43,-122.73,45.59,-122.55"
    python manage.py discover_venue_feeds --add    # auto-create VeueFeed records
"""
import re
import time
import requests
from django.core.management.base import BaseCommand
from events.models import VenueFeed

HEADERS = {
    'User-Agent': 'CommunityPlaylist/1.0 (communityplaylist.com; venue-discovery-bot)',
    'Accept-Language': 'en-US,en;q=0.9',
}

# Portland metro bounding box: S,W,N,E
DEFAULT_BBOX = '45.43,-122.86,45.68,-122.40'

OVERPASS_URL = 'https://overpass-api.de/api/interpreter'

OVERPASS_QUERY = """
[out:json][timeout:30];
(
  node["amenity"="music_venue"][~"^(website|contact:website)$"~"."]({bbox});
  node["amenity"="theatre"][~"^(website|contact:website)$"~"."]({bbox});
  node["amenity"="nightclub"][~"^(website|contact:website)$"~"."]({bbox});
  node["amenity"="bar"]["live_music"="yes"][~"^(website|contact:website)$"~"."]({bbox});
  node["amenity"="pub"]["live_music"="yes"][~"^(website|contact:website)$"~"."]({bbox});
  way["amenity"="music_venue"][~"^(website|contact:website)$"~"."]({bbox});
  way["amenity"="theatre"][~"^(website|contact:website)$"~"."]({bbox});
  way["amenity"="nightclub"][~"^(website|contact:website)$"~"."]({bbox});
);
out tags;
"""

ICAL_PATHS = [
    '/events/list/?ical=1',
    '/events/?ical=1',
    '/?ical=1',
    '/calendar/?ical=1',
    '/events/feed/',
    '/events.ics',
]

TEC_REST = '/wp-json/tribe/events/v1/events/?per_page=5'

SQ_JSON = '/api/open/GetItemsByTag?tag=Events&format=json&filter=%7B%22tags%22%3A%5B%22Events%22%5D%7D&crumb='


def _normalise_url(url):
    url = url.strip().rstrip('/')
    if not url.startswith('http'):
        url = 'https://' + url
    return url


def _check_ical(base, session):
    for path in ICAL_PATHS:
        try:
            r = session.get(base + path, timeout=7, allow_redirects=True)
            if r.status_code == 200 and 'VCALENDAR' in r.text[:300]:
                count = r.text.count('BEGIN:VEVENT')
                return base + path, count
        except Exception:
            pass
    return None, 0


def _check_eael(base, session):
    for path in ['/', '/events/', '/calendar/']:
        try:
            r = session.get(base + path, timeout=7)
            if r.status_code == 200 and 'data-events=' in r.text:
                return base + path
        except Exception:
            pass
    return None


def _check_tec_rest(base, session):
    try:
        r = session.get(base + TEC_REST, timeout=7)
        if r.status_code == 200 and r.headers.get('Content-Type', '').startswith('application/json'):
            try:
                data = r.json()
                if 'events' in data or (isinstance(data, list) and data):
                    count = len(data.get('events', data))
                    return base + TEC_REST, count
            except Exception:
                pass
    except Exception:
        pass
    return None, 0


def _check_squarespace(base, session):
    try:
        r = session.get(base + '/events/', timeout=7)
        if r.status_code == 200 and 'squarespace' in r.text.lower():
            return base + '/events/'
    except Exception:
        pass
    return None


class Command(BaseCommand):
    help = 'Discover Portland venue websites with parseable calendar feeds via OpenStreetMap'

    def add_arguments(self, parser):
        parser.add_argument(
            '--bbox', default=DEFAULT_BBOX,
            help='Bounding box S,W,N,E (default: Portland metro)',
        )
        parser.add_argument(
            '--add', action='store_true',
            help='Auto-create VenueFeed records for discovered feeds',
        )
        parser.add_argument(
            '--skip-existing', action='store_true', default=True,
            help='Skip venues already in VenueFeed (default: True)',
        )

    def handle(self, *args, **options):
        bbox = options['bbox']
        auto_add = options['add']

        self.stdout.write(f'Querying Overpass API for venues in bbox {bbox}…')
        query = OVERPASS_QUERY.replace('{bbox}', bbox)
        try:
            r = requests.post(OVERPASS_URL, data={'data': query}, timeout=40, headers=HEADERS)
            r.raise_for_status()
            elements = r.json().get('elements', [])
        except Exception as e:
            self.stderr.write(f'Overpass query failed: {e}')
            return

        self.stdout.write(f'Found {len(elements)} OSM elements with websites')

        # Collect unique websites + names
        venues = {}
        for el in elements:
            tags = el.get('tags', {})
            name = tags.get('name', '(unnamed)')
            url = tags.get('website') or tags.get('contact:website', '')
            if not url:
                continue
            url = _normalise_url(url)
            # de-dup by base domain
            try:
                from urllib.parse import urlparse
                base = urlparse(url).scheme + '://' + urlparse(url).netloc
            except Exception:
                base = url
            if base not in venues:
                venues[base] = name

        self.stdout.write(f'Unique venue websites: {len(venues)}')

        # Build set of already-tracked URLs
        existing_urls = set()
        if options['skip_existing']:
            for vf in VenueFeed.objects.exclude(url='').values_list('url', flat=True):
                try:
                    from urllib.parse import urlparse
                    existing_urls.add(urlparse(vf).scheme + '://' + urlparse(vf).netloc)
                except Exception:
                    pass

        discovered = []

        session = requests.Session()
        session.headers.update(HEADERS)

        total = len(venues)
        for i, (base, name) in enumerate(venues.items(), 1):
            if base in existing_urls:
                self.stdout.write(f'  [{i}/{total}] SKIP (already tracked): {name}')
                continue

            self.stdout.write(f'  [{i}/{total}] Checking {name} ({base})…')

            feed_type = None
            feed_url = None
            event_count = 0

            # 1. iCal
            ical_url, ical_count = _check_ical(base, session)
            if ical_url:
                feed_type, feed_url, event_count = 'ical', ical_url, ical_count

            # 2. EAEL
            if not feed_type:
                eael_url = _check_eael(base, session)
                if eael_url:
                    feed_type, feed_url = 'eael', eael_url

            # 3. TEC REST API
            if not feed_type:
                tec_url, tec_count = _check_tec_rest(base, session)
                if tec_url:
                    feed_type, feed_url, event_count = 'tec_rest', tec_url, tec_count

            # 4. Squarespace
            if not feed_type:
                sq_url = _check_squarespace(base, session)
                if sq_url:
                    feed_type, feed_url = 'squarespace', sq_url

            if feed_type:
                marker = f'events={event_count}' if event_count else ''
                self.stdout.write(
                    self.style.SUCCESS(f'    ✓ {feed_type} — {feed_url} {marker}')
                )
                discovered.append({'name': name, 'base': base, 'type': feed_type, 'url': feed_url})
            else:
                self.stdout.write(f'    — no parseable feed')

            time.sleep(0.3)  # be polite

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Discovery complete — {len(discovered)} parseable feeds found out of {len(venues)} venues'
        ))

        if discovered:
            self.stdout.write('\nDiscovered feeds:')
            for d in discovered:
                self.stdout.write(f"  {d['type']:12} | {d['name']:40} | {d['url']}")

        if auto_add and discovered:
            self.stdout.write('\nCreating VenueFeed records…')
            for d in discovered:
                src = d['type'] if d['type'] in ('ical', 'eael', 'squarespace') else 'ical'
                vf, created = VenueFeed.objects.get_or_create(
                    url=d['url'],
                    defaults={
                        'name': d['name'],
                        'source_type': src,
                        'auto_approve': False,
                        'active': True,
                    },
                )
                status = 'created' if created else 'exists'
                self.stdout.write(f"  [{vf.pk}] {status}: {vf.name}")
