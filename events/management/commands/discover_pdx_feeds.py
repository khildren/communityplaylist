"""
management command: python manage.py discover_pdx_feeds

Self-discovery for PDX event sources:
  1. Tries to activate/fix inactive iCal feeds by testing and sniffing their venue websites
  2. Mines website URLs from already-imported events and tries to find iCal feeds there
  3. Adds newly found feeds as inactive VenueFeeds (admin review before enabling)

Run monthly via cron (after recheck_venue_feeds):
  5 8 1 * *  /path/venv/bin/python /path/manage.py discover_pdx_feeds >> /var/log/cp_discover_feeds.log 2>&1
"""
from django.core.management.base import BaseCommand
from django.db.models import Q
from events.models import VenueFeed, Event
from icalendar import Calendar
import requests
import re
from urllib.parse import urlparse

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; CommunityPlaylist/1.0; +https://communityplaylist.com)'}
TIMEOUT = 6

ICAL_PATTERNS = [
    '{base}/events/list/?ical=1',
    '{base}/events/?ical=1',
    '{base}/calendar/?ical=1',
    '{base}/events.ics',
    '{base}/calendar.ics',
    '{base}/?ical=1',
    '{base}/events/feed/ical/',
]

ICAL_RE = [
    re.compile(r'<link[^>]+type=["\']text/calendar["\'][^>]*href=["\']([^"\']+)["\']', re.I),
    re.compile(r'<link[^>]+href=["\']([^"\']+)["\'][^>]*type=["\']text/calendar["\']', re.I),
    re.compile(r'href=["\']([^"\']*\.ics(?:\?[^"\']*)?)["\']', re.I),
    re.compile(r'href=["\']([^"\']*[?&]ical=1[^"\']*)["\']', re.I),
]


def base_url(url):
    p = urlparse(url)
    return f'{p.scheme}://{p.netloc}'


def is_valid_ical(content):
    try:
        Calendar.from_ical(content)
        return True
    except Exception:
        return False


def test_url(url):
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
        return r.status_code == 200 and is_valid_ical(r.content)
    except Exception:
        return False


def sniff_ical_from_html(html, site_base):
    found = set()
    for pattern in ICAL_RE:
        for match in pattern.findall(html):
            url = match.strip()
            if url.startswith('//'):
                url = 'https:' + url
            elif url.startswith('/'):
                url = site_base + url
            elif not url.startswith('http'):
                url = site_base + '/' + url
            found.add(url)
    return found


def discover_ical_for_website(website, stdout):
    """Try to find a working iCal feed URL for a website. Returns URL or None."""
    try:
        site = base_url(website)
    except Exception:
        return None

    # Sniff HTML on events pages first
    for path in ['', '/events', '/calendar']:
        try:
            r = requests.get(site + path, timeout=TIMEOUT, headers=HEADERS)
            if r.status_code == 200 and 'text/html' in r.headers.get('content-type', ''):
                for url in sniff_ical_from_html(r.text, site):
                    if test_url(url):
                        stdout.write(f'    found via HTML sniff: {url}')
                        return url
            elif r.status_code not in (200, 404):
                break  # server error / redirect loop — skip patterns
        except Exception:
            return None  # site down or unreachable

    # Try common CMS patterns
    for pattern in ICAL_PATTERNS:
        url = pattern.format(base=site)
        if test_url(url):
            stdout.write(f'    found via pattern: {url}')
            return url

    return None


class Command(BaseCommand):
    help = 'Auto-discover PDX iCal event feeds from existing venues and imported events'

    def handle(self, *args, **options):
        fixed = discovered = 0
        known_urls  = set(VenueFeed.objects.exclude(url='').values_list('url', flat=True))
        known_sites = set(
            urlparse(w).netloc
            for w in VenueFeed.objects.exclude(website='').values_list('website', flat=True)
            if w
        )

        # ── Phase 1: Activate/fix inactive iCal feeds ───────────────────────
        self.stdout.write('\n-- Phase 1: Testing inactive feeds --')
        inactive = VenueFeed.objects.filter(active=False, source_type='ical')
        self.stdout.write(f'  {inactive.count()} inactive feeds to check...')

        for feed in inactive:
            self.stdout.write(f'  {feed.name}...', ending=' ')

            # If URL already set, test it directly first
            if feed.url and test_url(feed.url):
                feed.active = True
                feed.last_error = ''
                feed.save(update_fields=['active', 'last_error'])
                self.stdout.write(self.style.SUCCESS('ACTIVATED (existing URL now works)'))
                fixed += 1
                continue

            # Try to discover a new URL via website crawl
            if feed.website:
                url = discover_ical_for_website(feed.website, self.stdout)
                if url:
                    feed.url = url
                    feed.active = True
                    feed.last_error = ''
                    feed.save(update_fields=['url', 'active', 'last_error'])
                    known_urls.add(url)
                    self.stdout.write(self.style.SUCCESS('FIXED'))
                    fixed += 1
                    continue

            self.stdout.write('no iCal found')

        # ── Phase 2: Mine imported events for new venue websites ─────────────
        self.stdout.write('\n-- Phase 2: Mining imported events for venue websites --')

        # Collect unique website URLs from imported events
        imported_websites = (
            Event.objects
            .exclude(website='')
            .exclude(submitted_by='')
            .values_list('website', flat=True)
            .distinct()
        )

        candidates = {}  # netloc -> (website_url, submitted_by)
        for website in imported_websites:
            try:
                netloc = urlparse(website).netloc
                if netloc and netloc not in known_sites and netloc not in candidates:
                    candidates[netloc] = website
            except Exception:
                continue

        self.stdout.write(f'  Found {len(candidates)} new venue websites from imported events')

        for netloc, website in candidates.items():
            self.stdout.write(f'  Trying {netloc}...', ending=' ')
            url = discover_ical_for_website(website, self.stdout)
            if url and url not in known_urls:
                name_base = netloc.replace('www.', '').split('.')[0]
                name = f'Auto-discovered: {name_base.replace("-", " ").title()}'
                VenueFeed.objects.create(
                    name=name,
                    website=f'https://{netloc}',
                    url=url,
                    source_type='ical',
                    active=False,
                    notes='Auto-discovered from imported event data. Review and enable in admin.',
                )
                known_urls.add(url)
                known_sites.add(netloc)
                self.stdout.write(self.style.SUCCESS(f'ADDED: {url}'))
                discovered += 1
            else:
                self.stdout.write('no iCal found')

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. {fixed} feeds activated/fixed, {discovered} new feeds discovered.'
        ))
        if discovered:
            self.stdout.write('Review new feeds at /admin/events/venuefeed/ before enabling.')
