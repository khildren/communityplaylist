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
from django.core.files.base import ContentFile
from events.models import VenueFeed, Event, RecurringEvent, Genre
from events.enrich import enrich_event, clean_text
import requests
from icalendar import Calendar
from datetime import datetime, date, timedelta
import mimetypes
import os
import re
import pytz

PDX_TZ = pytz.timezone('America/Los_Angeles')

# ── Cross-feed dedup helpers ──────────────────────────────────────────────────

def _norm_title(title):
    """Normalize a title for cross-feed duplicate detection.

    Collapses "&" / "and" / "+" variants, strips accidental punctuation,
    so "Subduction Audio & Friends" and "Subduction Audio and Friends"
    produce the same fingerprint.
    """
    t = title.lower().strip()
    t = re.sub(r'\s*&\s*', ' and ', t)   # & → and
    t = re.sub(r'\s*\+\s*', ' and ', t)  # + → and
    t = re.sub(r"[''`]", '', t)           # smart quotes
    t = re.sub(r'[^\w\s]', ' ', t)       # strip remaining punctuation
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def _same_day_title_exists(title, dtstart):
    """Return an existing Event that has the same normalized title on the same
    calendar day (±30-hour window to absorb timezone shifts between feeds)."""
    norm = _norm_title(title)
    day = dtstart.replace(hour=0, minute=0, second=0, microsecond=0)
    window_start = day - timedelta(hours=6)
    window_end   = day + timedelta(hours=30)
    candidates = Event.objects.filter(
        start_date__gte=window_start,
        start_date__lt=window_end,
    ).only('id', 'title', 'start_date')
    for ev in candidates:
        if _norm_title(ev.title) == norm:
            return ev
    return None


IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}
IMAGE_EXTS  = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}


def download_image(url, field_name='photo'):
    """Download an image URL, return (filename, ContentFile) or (None, None)."""
    try:
        r = requests.get(url, timeout=10, stream=True,
                         headers={'User-Agent': 'CommunityPlaylist/1.0 (communityplaylist.com)'})
        r.raise_for_status()
        ct = r.headers.get('content-type', '').split(';')[0].strip().lower()
        if ct not in IMAGE_TYPES:
            return None, None
        ext = mimetypes.guess_extension(ct) or '.jpg'
        ext = ext.replace('.jpe', '.jpg')
        fname = f"{field_name}_{slugify(url[-40:])}{ext}"
        return fname, ContentFile(r.content)
    except Exception:
        return None, None


def tag_feed_defaults(ev, feed):
    """Apply default genres and resident artists from the feed to an event."""
    if ev.category == 'music':
        genres = feed.default_genres.all()
        if genres:
            ev.genres.add(*genres)
    residents = feed.residents.all()
    if residents:
        ev.artists.add(*residents)
    if feed.promoter_id:
        ev.promoters.add(feed.promoter)
    # Auto-set music category when genres are present and no category assigned
    if not ev.category and ev.genres.exists():
        ev.category = 'music'
        ev.save(update_fields=['category'])


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
            summary    = clean_text(str(component.get('summary', '')).strip(), max_len=200)
            dtstart    = to_aware(component.get('dtstart').dt)
            dtend_raw  = component.get('dtend')
            dtend      = to_aware(dtend_raw.dt) if dtend_raw else None
            desc       = clean_text(str(component.get('description', '')).strip())
            location   = str(component.get('location', '')).strip()
            url_prop   = str(component.get('url', '')).strip()
            attach_raw = component.get('attach')
            image_url  = None
            if attach_raw:
                attach_str = str(attach_raw).strip()
                ext = os.path.splitext(attach_str.split('?')[0])[-1].lower()
                if attach_str.startswith('http') and ext in IMAGE_EXTS:
                    image_url = attach_str

            uid = str(component.get('uid', '')).strip()

            if not summary or not dtstart:
                continue
            if dtstart < now:
                continue

            # Dedup: UID stored in website field → slug → normalized title+day.
            existing = None
            if uid:
                existing = Event.objects.filter(website=uid[:200]).first()
            if not existing:
                slug_base = slugify(f"{summary}-{dtstart.strftime('%Y-%m-%d')}")
                existing = Event.objects.filter(slug__startswith=slug_base).first()
            if not existing:
                existing = _same_day_title_exists(summary, dtstart)

            if existing:
                # Patch mutable fields if the upstream event was edited
                changed = {}
                if existing.title != summary[:200]:
                    changed['title'] = summary[:200]
                if existing.start_date != dtstart:
                    changed['start_date'] = dtstart
                if dtend and existing.end_date != dtend:
                    changed['end_date'] = dtend
                if uid and existing.website != uid[:200]:
                    changed['website'] = uid[:200]
                if changed:
                    for k, v in changed.items():
                        setattr(existing, k, v)
                    existing.save(update_fields=list(changed.keys()))
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
                # Store UID as stable dedup key; url_prop already in description
                website=uid[:200] if uid else (url_prop[:200] if url_prop else ''),
            )
            if image_url:
                fname, content = download_image(image_url)
                if fname and content:
                    ev.photo.save(fname, content, save=True)
            _, out_of_area = enrich_event(ev, geocode=bool(location), save=True)
            if out_of_area:
                ev.status = 'rejected'
                ev.save(update_fields=['status'])
                skipped += 1
                created -= 0  # don't count as created
                continue
            tag_feed_defaults(ev, feed)
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
                _, out_of_area = enrich_event(ev, geocode=bool(location), save=True)
                if out_of_area:
                    ev.status = 'rejected'
                    ev.save(update_fields=['status'])
                    skipped += 1
                    continue
                tag_feed_defaults(ev, feed)
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


def import_squarespace(feed, now, stdout, stderr):
    """Import events from a Squarespace site's ?format=json events endpoint.
    feed.url should be the full events page URL, e.g. https://www.livinghausbeer.com/events

    Recurring-event detection: a title that appears 3+ times in the feed with
    short per-occurrence duration (<= 1 day) is a genuine repeating night and gets
    converted to a RecurringEvent. A single long-span entry (festival, trail, etc.)
    is imported as a normal Event — span alone is not enough to infer recurrence.
    """
    import re as _re
    from collections import Counter
    from datetime import datetime as dt_class, timedelta

    # Minimum number of times a title must appear in the feed before we treat
    # it as a recurring series rather than a single multi-day event.
    RECURRING_MIN_OCCURRENCES = 3
    # Per-occurrence duration must be at most this many hours to qualify.
    RECURRING_MAX_HOURS_PER_OCC = 24

    created = skipped = 0
    status = 'approved' if feed.auto_approve else 'pending'

    base_url = feed.url.rstrip('/')
    json_url = f"{base_url}?format=json"

    # ── Pass 1: collect all raw event dicts across all pages ──────────────────
    all_raw = []
    next_offset = None
    while True:
        url = json_url if next_offset is None else f"{base_url}?format=json&offset={next_offset}"
        try:
            r = requests.get(url, timeout=15, headers={
                'Accept': 'application/json',
                'User-Agent': 'CommunityPlaylist/1.0 (communityplaylist.com)',
            })
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return 0, 0, str(e)

        page_events = data.get('upcoming', []) + data.get('past', [])
        all_raw.extend(page_events)

        pagination = data.get('pagination', {})
        if not pagination.get('nextPage'):
            break
        next_page_url = pagination.get('nextPageUrl', '')
        m = _re.search(r'offset=(\d+)', next_page_url)
        next_offset = m.group(1) if m else None
        if not next_offset:
            break

    if not all_raw:
        return 0, 0, ''

    # ── Identify truly recurring titles ───────────────────────────────────────
    # A title qualifies if it appears >= RECURRING_MIN_OCCURRENCES times AND
    # each occurrence has a short duration (i.e. it's a regular weekly night,
    # not a single multi-week festival listed multiple times by mistake).
    title_entries: dict = {}
    for ev in all_raw:
        title = (ev.get('title') or '').strip()
        if not title:
            continue
        start_ms = ev.get('startDate') or 0
        end_ms   = ev.get('endDate') or 0
        if not start_ms:
            continue
        hours = (end_ms - start_ms) / 3_600_000 if end_ms else 0
        title_entries.setdefault(title, []).append(hours)

    recurring_titles = {
        t for t, durations in title_entries.items()
        if len(durations) >= RECURRING_MIN_OCCURRENCES
        and all(0 < h <= RECURRING_MAX_HOURS_PER_OCC for h in durations)
    }

    recurring_created_titles: set = set()  # guard against creating the same RecurringEvent twice

    # ── Pass 2: process events ─────────────────────────────────────────────────
    for ev in all_raw:
        try:
            title     = (ev.get('title') or '').strip()
            start_ms  = ev.get('startDate') or 0
            end_ms    = ev.get('endDate') or 0
            full_url  = ev.get('fullUrl') or ''
            asset_url = ev.get('assetUrl') or ''
            body      = ev.get('body') or ''

            if not title or not start_ms:
                continue

            dtstart = pytz.utc.localize(dt_class.utcfromtimestamp(start_ms / 1000)).astimezone(PDX_TZ)
            dtend   = pytz.utc.localize(dt_class.utcfromtimestamp(end_ms / 1000)).astimezone(PDX_TZ) if end_ms else None

            location  = feed.notes.strip() if feed.notes.strip() else feed.name
            event_url = f"https://{feed.url.split('/')[2]}{full_url}" if full_url.startswith('/') else full_url
            clean_body = _re.sub(r'<[^>]+>', ' ', body).strip()[:1000]
            description = clean_body or f'Event at {feed.name}'
            if event_url:
                description = f'{description}\n\nMore info: {event_url}'.strip()[:2000]

            # ── Genuinely recurring title → RecurringEvent (one per title) ───
            if title in recurring_titles:
                if title in recurring_created_titles:
                    skipped += 1
                    continue
                if RecurringEvent.objects.filter(title=title[:200], location=location[:300]).exists():
                    recurring_created_titles.add(title)
                    skipped += 1
                    continue
                rec = RecurringEvent.objects.create(
                    title=title[:200],
                    description=description[:2000],
                    location=location[:300],
                    category=feed.default_category or '',
                    is_free=False,
                    website=event_url[:200],
                    frequency=RecurringEvent.FREQ_WEEKLY,
                    interval=1,
                    day_of_week=dtstart.weekday(),
                    start_time=dtstart.time(),
                    duration_minutes=120,
                    submitted_by=feed.name,
                    auto_approve=feed.auto_approve,
                    active=True,
                    lookahead_weeks=16,
                )
                if feed.default_genres.exists():
                    rec.genres.set(feed.default_genres.all())
                recurring_created_titles.add(title)
                stdout.write(f'    → recurring: {title!r} (every {dtstart.strftime("%A")})')
                created += 1
                continue

            # ── Single / multi-day event ──────────────────────────────────────
            if dtstart < now:
                skipped += 1
                continue

            # Dedup: match on stable website URL first, then fall back to slug.
            # URL match also handles updates — promoter edits title → new slug
            # but same URL, so we update the existing record instead of duping.
            existing = None
            if event_url:
                existing = Event.objects.filter(website=event_url[:200]).first()
            if not existing:
                slug_base = slugify(f"{title}-{dtstart.strftime('%Y-%m-%d')}")
                existing = Event.objects.filter(slug__startswith=slug_base).first()

            if existing:
                # Update mutable fields if they changed
                changed = {}
                if existing.title != title[:200]:
                    changed['title'] = title[:200]
                if existing.start_date != dtstart:
                    changed['start_date'] = dtstart
                if existing.end_date != dtend:
                    changed['end_date'] = dtend
                if event_url and existing.website != event_url[:200]:
                    changed['website'] = event_url[:200]
                if changed:
                    for k, v in changed.items():
                        setattr(existing, k, v)
                    existing.save(update_fields=list(changed.keys()))
                    stdout.write(f'    → updated: {title!r} ({", ".join(changed)})')
                skipped += 1
                continue

            new_ev = Event.objects.create(
                title=title[:200],
                description=description[:2000],
                location=location[:300],
                start_date=dtstart,
                end_date=dtend,
                submitted_by=feed.name,
                submitted_email='',
                status=status,
                is_free=False,
                category=feed.default_category,
                website=event_url[:200],
            )
            if asset_url:
                fname, content = download_image(asset_url)
                if fname and content:
                    new_ev.photo.save(fname, content, save=True)
            _, out_of_area = enrich_event(new_ev, geocode=bool(location), save=True)
            if out_of_area:
                new_ev.status = 'rejected'
                new_ev.save(update_fields=['status'])
                skipped += 1
                continue
            tag_feed_defaults(new_ev, feed)
            created += 1
        except Exception as e:
            stderr.write(f'    skipping event: {e}')
            continue

    return created, skipped, ''


def import_eventbrite(feed, now, stdout, stderr):
    """Query Eventbrite API for Portland events. Returns (created, skipped, error_str).

    NOTE: Eventbrite deprecated the /v3/events/search/ public endpoint in 2023.
    Personal tokens no longer have access. Instead, add individual Eventbrite
    organiser iCal feeds as iCal-type VenueFeeds:
      https://www.eventbrite.com/o/<organizer-slug>/icalendar.ics
    This function is kept for forward-compatibility but returns early.
    """
    return 0, 0, 'Eventbrite public search API deprecated — use iCal feeds per organiser instead'

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
                title      = ev.get('name', {}).get('text', '').strip()
                desc       = ev.get('description', {}).get('text', '') or ev.get('summary', '') or ''
                ev_url     = ev.get('url', '')
                start_raw  = ev.get('start', {}).get('utc', '')
                end_raw    = ev.get('end', {}).get('utc', '')
                logo_url   = (ev.get('logo') or {}).get('url') or \
                             (ev.get('logo') or {}).get('original', {}).get('url')

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
                if logo_url:
                    fname, content = download_image(logo_url)
                    if fname and content:
                        ev.photo.save(fname, content, save=True)
                _, out_of_area = enrich_event(ev, geocode=bool(location), save=True)
                if out_of_area:
                    ev.status = 'rejected'
                    ev.save(update_fields=['status'])
                    skipped += 1
                    continue
                tag_feed_defaults(ev, feed)
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


def import_19hz(feed, now, stdout, stderr):
    """
    Scrape the 19hz.info PNW electronic music event listing.

    The page is a plain HTML table — no API key, fully open, community-run.
    Only imports Oregon events (Portland-area). Filters out past events and
    deduplicates by title+date slug as usual.

    Attribution: source link is stored in event.website so the 19hz link
    is always visible on the event detail page, honoring the community spirit.
    """
    import re as _re
    from datetime import datetime as dt_class

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return 0, 0, 'beautifulsoup4 not installed — run: pip install beautifulsoup4'

    URL = 'https://19hz.info/eventlisting_PNW.php'
    try:
        r = requests.get(URL, timeout=15, headers={'User-Agent': 'CommunityPlaylist/1.0 (communityplaylist.com)'})
        r.raise_for_status()
    except Exception as e:
        return 0, 0, str(e)

    soup = BeautifulSoup(r.text, 'html.parser')
    tables = soup.find_all('table')
    if not tables:
        return 0, 0, 'No tables found on 19hz page'

    main_table = tables[0]
    rows = main_table.find_all('tr')

    created = skipped = 0
    status  = 'approved' if feed.auto_approve else 'pending'
    PDX_TZ  = pytz.timezone('America/Los_Angeles')

    for row in rows[1:]:  # skip header
        try:
            cells = row.find_all('td', recursive=False)
            if not cells or len(cells) < 2:
                continue

            # ── Date from div.shrink (ISO format 2026/04/06) ──────────────────
            date_div = row.find('div', class_='shrink')
            if not date_div:
                continue
            date_str = date_div.get_text(strip=True)  # e.g. "2026/04/06"
            try:
                event_date = dt_class.strptime(date_str, '%Y/%m/%d').date()
            except ValueError:
                continue

            # ── Time from cell[0] text ─────────────────────────────────────────
            time_text = cells[0].get_text(' ', strip=True)
            # e.g. "Mon: Apr 6 (6:30pm-10pm)" or "Mon: Apr 6 (8pm)"
            time_match = _re.search(r'\((\d{1,2}(?::\d{2})?(?:am|pm))', time_text, _re.I)
            if time_match:
                raw_time = time_match.group(1)
                try:
                    if ':' in raw_time:
                        t = dt_class.strptime(raw_time, '%I:%M%p').time()
                    else:
                        t = dt_class.strptime(raw_time, '%I%p').time()
                except ValueError:
                    t = dt_class.strptime('8:00pm', '%I:%M%p').time()
            else:
                t = dt_class.strptime('8:00pm', '%I:%M%p').time()

            dtstart = PDX_TZ.localize(dt_class.combine(event_date, t))

            # Skip past events
            if dtstart < now:
                skipped += 1
                continue

            # ── Title & ticket link from first <a> in cell[1] ────────────────
            title_cell = cells[1]
            first_link = title_cell.find('a')
            if not first_link:
                continue
            title    = first_link.get_text(strip=True)
            tick_url = first_link.get('href', '').strip()
            if not title:
                continue

            # ── Venue: text after "@ " in cell[1], before nested <td> ─────────
            # NavigableStrings in cell[1] give us the raw text nodes
            venue = ''
            for node in title_cell.children:
                text = getattr(node, 'string', None) or ''
                text = str(text).strip()
                if '@ ' in text:
                    venue = text.split('@ ', 1)[1].strip()
                    break

            # ── Filter: Oregon only (skip WA, ID, etc.) ───────────────────────
            if venue and not _re.search(r',\s*OR\b', venue, _re.I):
                skipped += 1
                continue
            # If no venue found, assume Portland (rare — only RA-linked events)
            location = venue or 'Portland, OR'

            # ── Price / age from nested <td> ───────────────────────────────────
            nested_tds = title_cell.find_all('td')
            price_text = nested_tds[1].get_text(strip=True) if len(nested_tds) > 1 else ''
            is_free    = bool(_re.search(r'\bfree\b', price_text, _re.I))

            # ── Genre tags ────────────────────────────────────────────────────
            tags_text = nested_tds[0].get_text(strip=True) if nested_tds else ''

            # ── Organiser ─────────────────────────────────────────────────────
            organiser = nested_tds[2].get_text(strip=True) if len(nested_tds) > 2 else ''

            # ── Dedup ─────────────────────────────────────────────────────────
            slug_base = slugify(f"{title}-{event_date.strftime('%Y-%m-%d')}")
            if Event.objects.filter(slug__startswith=slug_base).exists():
                skipped += 1
                continue

            desc_parts = []
            if price_text:
                desc_parts.append(f'Price/ages: {price_text}')
            if organiser:
                desc_parts.append(f'Organiser: {organiser}')
            if tick_url:
                desc_parts.append(f'Tickets / info: {tick_url}')
            description = '\n'.join(desc_parts)[:2000]

            ev = Event.objects.create(
                title           = title[:200],
                description     = description,
                location        = location[:300],
                start_date      = dtstart,
                submitted_by    = '19hz.info',
                submitted_email = '',
                status          = status,
                is_free         = is_free,
                category        = feed.default_category or 'music',
                website         = tick_url[:200] if tick_url else URL,
            )
            enrich_event(ev, geocode=False, save=True)  # geocode_events cron handles this
            tag_feed_defaults(ev, feed)

            # Parse genre/tags from 19hz and apply as Genre objects
            if tags_text:
                _STOP = {'and','or','the','a','an','of','in','at','by','for','with','on'}
                for raw in tags_text.split(','):
                    raw = raw.strip()
                    if not raw:
                        continue
                    # Smart title-case: keep hyphenated word caps, don't lowercase stopwords mid-hyphen
                    parts = raw.split()
                    tag = ' '.join(
                        p if '-' in p or '&' in p else
                        (p.lower() if i > 0 and p.lower() in _STOP else p.capitalize())
                        for i, p in enumerate(parts)
                    )
                    if tag:
                        genre_obj, _ = Genre.objects.get_or_create(name=tag)
                        ev.genres.add(genre_obj)
            created += 1

        except Exception as e:
            stderr.write(f'    skipping 19hz row: {e}')
            continue

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
            elif feed.source_type == VenueFeed.SOURCE_SQUARESPACE:
                if not feed.url:
                    self.stderr.write('  no URL — skipping')
                    continue
                created, skipped, error = import_squarespace(feed, now, self.stdout, self.stderr)
            elif feed.source_type == VenueFeed.SOURCE_19HZ:
                created, skipped, error = import_19hz(feed, now, self.stdout, self.stderr)
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
