"""
management command: python manage.py import_venue_feeds

Pulls events from all active VenueFeed sources (iCal, MusicBrainz API, Eventbrite API).
Creates Event records status=pending unless auto_approve=True.

Run twice a week via cron:
  0 7 * * 1,4  /path/venv/bin/python /path/manage.py import_venue_feeds
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify
from django.conf import settings
from events.models import VenueFeed, Event
from events.enrich import enrich_event
import requests
from icalendar import Calendar
from datetime import datetime, date
import pytz

PDX_TZ = pytz.timezone('America/Los_Angeles')


def to_aware(dt):
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return PDX_TZ.localize(dt)
        return dt.astimezone(PDX_TZ)
    if isinstance(dt, date):
        return PDX_TZ.localize(datetime(dt.year, dt.month, dt.day, 0, 0))
    return None


def import_ical(feed, now, stdout, stderr):
    """Fetch and parse an iCal feed. Returns (created, skipped, error_str)."""
    try:
        r = requests.get(
            feed.url,
            timeout=15,
            headers={'User-Agent': 'CommunityPlaylist/1.0 (communityplaylist.com)'},
        )
        r.raise_for_status()
        cal = Calendar.from_ical(r.content)
    except Exception as e:
        return 0, 0, str(e)

    created = skipped = 0
    status = 'approved' if feed.auto_approve else 'pending'

    for component in cal.walk():
        if component.name != 'VEVENT':
            continue
        try:
            summary   = str(component.get('summary', '')).strip()
            dtstart   = to_aware(component.get('dtstart').dt)
            dtend_raw = component.get('dtend')
            dtend     = to_aware(dtend_raw.dt) if dtend_raw else None
            desc      = str(component.get('description', '')).strip()
            location  = str(component.get('location', '')).strip()
            url_prop  = str(component.get('url', '')).strip()

            if not summary or not dtstart:
                continue
            if dtstart < now:
                continue

            slug_base = slugify(f"{summary}-{dtstart.strftime('%Y-%m-%d')}")
            if Event.objects.filter(slug__startswith=slug_base).exists():
                skipped += 1
                continue

            description = desc[:2000] if desc else f'Imported from {feed.name}'
            if url_prop and url_prop not in description:
                description = f'{description}\n\nEvent link: {url_prop}'.strip()

            ev = Event.objects.create(
                title=summary[:200],
                description=description[:2000],
                location=location[:300],
                start_date=dtstart,
                end_date=dtend,
                submitted_by=feed.name,
                submitted_email='',
                status=status,
                is_free=False,
                category=feed.default_category,
                website=url_prop[:200] if url_prop else '',
            )
            enrich_event(ev, geocode=bool(location), save=True)
            created += 1
        except Exception as e:
            stderr.write(f'    skipping event: {e}')
            continue

    return created, skipped, ''


def import_musicbrainz(feed, now, stdout, stderr):
    """Query MusicBrainz for upcoming Portland events. Free, open, no key required.
    Rate limited to 1 req/sec — sleeps between pages automatically."""
    import time as time_mod

    contact = getattr(settings, 'MUSICBRAINZ_CONTACT', 'hello@communityplaylist.com')
    headers = {
        'User-Agent': f'CommunityPlaylist/1.0 ( {contact} )',
        'Accept': 'application/json',
    }

    created = skipped = 0
    status = 'approved' if feed.auto_approve else 'pending'
    offset = 0
    limit = 100

    while True:
        try:
            resp = requests.get(
                'https://musicbrainz.org/ws/2/event',
                params={
                    'query': 'place:Portland AND area:Oregon',
                    'fmt': 'json',
                    'limit': limit,
                    'offset': offset,
                },
                headers=headers,
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            return created, skipped, str(e)

        events = data.get('events', [])
        if not events:
            break

        for ev in events:
            try:
                title    = ev.get('name', '').strip()
                ev_type  = ev.get('type', '')
                lifespan = ev.get('life-span', {})
                date_str = lifespan.get('begin', '')
                time_str = ev.get('time', '') or '19:00'

                if not title or not date_str:
                    continue

                try:
                    d = datetime.strptime(date_str, '%Y-%m-%d')
                except ValueError:
                    continue

                try:
                    t = datetime.strptime(time_str[:5], '%H:%M')
                except ValueError:
                    t = datetime.strptime('19:00', '%H:%M')

                dtstart = PDX_TZ.localize(d.replace(hour=t.hour, minute=t.minute))
                if dtstart < now:
                    skipped += 1
                    continue

                location = 'Portland, OR'
                for rel in ev.get('relations', []):
                    if rel.get('type') == 'held at' and rel.get('place'):
                        place = rel['place']
                        location = place.get('name', 'Portland, OR')
                        addr = place.get('address', '')
                        if addr:
                            location = f"{location}, {addr}"
                        location = location[:300]
                        break

                performers = []
                for rel in ev.get('relations', []):
                    entity = rel.get('artist') or rel.get('artist-credit')
                    if entity and rel.get('type') in ('performer', 'main performer', 'headliner'):
                        performers.append(entity.get('name', ''))

                mb_url = f"https://musicbrainz.org/event/{ev.get('id', '')}"
                display_title = title[:200]

                slug_base = slugify(f"{display_title}-{dtstart.strftime('%Y-%m-%d')}")
                if Event.objects.filter(slug__startswith=slug_base).exists():
                    skipped += 1
                    continue

                desc_parts = ['Imported from MusicBrainz']
                if ev_type:
                    desc_parts.append(f'Type: {ev_type}')
                if performers:
                    desc_parts.append(f'Performers: {", ".join(performers[:5])}')
                desc_parts.append(f'MusicBrainz: {mb_url}')
                description = '\n'.join(desc_parts)[:2000]

                category = 'music' if ev_type in ('Concert', 'Festival') else feed.default_category

                ev = Event.objects.create(
                    title=display_title,
                    description=description,
                    location=location,
                    start_date=dtstart,
                    submitted_by=feed.name,
                    submitted_email='',
                    status=status,
                    is_free=False,
                    category=category,
                    website=mb_url,
                )
                enrich_event(ev, geocode=bool(location), save=True)
                created += 1
            except Exception as e:
                stderr.write(f'    skipping MusicBrainz event: {e}')
                continue

        total = data.get('count', 0)
        offset += limit
        if offset >= total or offset >= 1000:
            break

        time_mod.sleep(1)  # MusicBrainz rate limit: 1 req/sec

    return created, skipped, ''


def import_eventbrite(feed, now, stdout, stderr):
    """Query Eventbrite API for Portland events. Returns (created, skipped, error_str)."""
    api_key = getattr(settings, 'EVENTBRITE_API_KEY', '')
    if not api_key:
        return 0, 0, 'EVENTBRITE_API_KEY not configured in settings'

    created = skipped = 0
    status = 'approved' if feed.auto_approve else 'pending'
    page = 1

    while True:
        try:
            resp = requests.get(
                'https://www.eventbriteapi.com/v3/events/search/',
                params={
                    'token': api_key,
                    'location.address': 'Portland, OR',
                    'location.within': '25mi',
                    'start_date.range_start': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    'expand': 'venue',
                    'page_size': 50,
                    'page': page,
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            return created, skipped, str(e)

        for ev in data.get('events', []):
            try:
                title     = ev.get('name', {}).get('text', '').strip()
                desc      = ev.get('description', {}).get('text', '') or ev.get('summary', '') or ''
                ev_url    = ev.get('url', '')
                start_raw = ev.get('start', {}).get('utc', '')
                end_raw   = ev.get('end', {}).get('utc', '')

                if not title or not start_raw:
                    continue

                dtstart = PDX_TZ.normalize(
                    pytz.utc.localize(datetime.strptime(start_raw, '%Y-%m-%dT%H:%M:%SZ')).astimezone(PDX_TZ)
                )
                dtend = PDX_TZ.normalize(
                    pytz.utc.localize(datetime.strptime(end_raw, '%Y-%m-%dT%H:%M:%SZ')).astimezone(PDX_TZ)
                ) if end_raw else None

                if dtstart < now:
                    skipped += 1
                    continue

                venue   = ev.get('venue') or {}
                address = venue.get('address', {})
                location = ', '.join(filter(None, [
                    venue.get('name', ''),
                    address.get('address_1', ''),
                    address.get('city', ''),
                ]))[:300]

                is_free = ev.get('is_free', False)

                slug_base = slugify(f"{title}-{dtstart.strftime('%Y-%m-%d')}")
                if Event.objects.filter(slug__startswith=slug_base).exists():
                    skipped += 1
                    continue

                description = desc[:2000] if desc else f'Imported from Eventbrite'
                if ev_url:
                    description = f'{description}\n\nTickets / info: {ev_url}'.strip()[:2000]

                ev = Event.objects.create(
                    title=title[:200],
                    description=description,
                    location=location,
                    start_date=dtstart,
                    end_date=dtend,
                    submitted_by=feed.name,
                    submitted_email='',
                    status=status,
                    is_free=is_free,
                    category=feed.default_category,
                    website=ev_url[:200],
                )
                enrich_event(ev, geocode=bool(location), save=True)
                created += 1
            except Exception as e:
                stderr.write(f'    skipping Eventbrite event: {e}')
                continue

        pagination = data.get('pagination', {})
        if not pagination.get('has_more_items'):
            break
        page += 1
        if page > 10:
            break

    return created, skipped, ''


class Command(BaseCommand):
    help = 'Import events from admin-managed venue/source feeds'

    def add_arguments(self, parser):
        parser.add_argument(
            '--feed', type=int, help='Import only the VenueFeed with this ID'
        )

    def handle(self, *args, **options):
        qs = VenueFeed.objects.filter(active=True)
        if options.get('feed'):
            qs = qs.filter(pk=options['feed'])

        now = timezone.now()
        total_created = total_skipped = 0

        for feed in qs:
            self.stdout.write(f'[{feed.source_type}] {feed.name}…')

            if feed.source_type == VenueFeed.SOURCE_ICAL:
                if not feed.url:
                    self.stderr.write('  no URL — skipping')
                    continue
                created, skipped, error = import_ical(feed, now, self.stdout, self.stderr)
            elif feed.source_type == VenueFeed.SOURCE_MUSICBRAINZ:
                created, skipped, error = import_musicbrainz(feed, now, self.stdout, self.stderr)
            elif feed.source_type == VenueFeed.SOURCE_EVENTBRITE:
                created, skipped, error = import_eventbrite(feed, now, self.stdout, self.stderr)
            else:
                error = f'unsupported source_type: {feed.source_type}'
                created = skipped = 0

            feed.last_synced = now
            feed.last_error = error
            feed.save(update_fields=['last_synced', 'last_error'])

            if error:
                self.stderr.write(f'  ERROR: {error}')
            else:
                self.stdout.write(f'  ✓ {created} created, {skipped} skipped')

            total_created += created
            total_skipped += skipped

        self.stdout.write(self.style.SUCCESS(
            f'Done. {total_created} events imported, {total_skipped} skipped.'
        ))
