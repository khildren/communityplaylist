"""
management command: python manage.py fetch_event_images [--limit N]

For approved events that have a website URL but no photo, fetches the event
page, extracts the og:image meta tag, then downloads and saves the image.

Run nightly — processes up to --limit events per run (default 30) to stay
polite. At 1 req/sec that's ~1 min of traffic spread across dozens of domains.

Cron (3am, after geocode):
  0 3 * * *  /path/venv/bin/python /path/manage.py fetch_event_images --limit 30
"""
import os
import re
import time
import mimetypes
from urllib.parse import urljoin, urlparse

from django.core.management.base import BaseCommand
from django.core.files.base import ContentFile
from django.utils.text import slugify
from django.utils import timezone

import requests

from events.models import Event

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (compatible; CommunityPlaylist/1.0; '
        '+https://communityplaylist.com)'
    )
}
IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}

# Domains that reliably 403, block crawlers, or have broken SSL — skip them
SKIP_DOMAINS = {
    'facebook.com', 'www.facebook.com',
    'instagram.com', 'www.instagram.com',
    'twitter.com', 'x.com',
    'musicbrainz.org',
    # Ticket platforms that 403 crawlers
    'app.tickettailor.com', 'tickettailor.com',
    'axs.com', 'www.axs.com',
    'ticketmaster.com', 'www.ticketmaster.com',
    'etix.com', 'www.etix.com',
    'dice.fm', 'www.dice.fm',
    'seetickets.us', 'www.seetickets.us',
}

OG_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
OG_RE2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    re.IGNORECASE,
)


def extract_og_image(html, base_url):
    m = OG_RE.search(html) or OG_RE2.search(html)
    if not m:
        return None
    url = m.group(1).strip()
    if not url:
        return None
    return urljoin(base_url, url)


def download_image(url):
    """Fetch image URL. Returns (filename, ContentFile) or (None, None)."""
    try:
        r = requests.get(url, timeout=10, stream=True, headers=HEADERS)
        r.raise_for_status()
        ct = r.headers.get('content-type', '').split(';')[0].strip().lower()
        if ct not in IMAGE_TYPES:
            return None, None
        ext = mimetypes.guess_extension(ct) or '.jpg'
        ext = ext.replace('.jpe', '.jpg')
        # Use last 40 chars of URL as filename base to keep it short
        slug = slugify(url.split('?')[0][-40:])
        fname = f"og_{slug}{ext}"
        return fname, ContentFile(r.content)
    except Exception:
        return None, None


class Command(BaseCommand):
    help = 'Fetch og:image from event websites for events missing a photo'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit', type=int, default=30,
            help='Max events to process per run (default 30)',
        )

    def handle(self, *args, **options):
        limit = options['limit']
        now = timezone.now()

        qs = (
            Event.objects
            .filter(status='approved', website__gt='')
            .exclude(photo__gt='')
            .order_by('start_date')  # tackle soonest events first
        )
        # Only bother with future + recent past (last 30 days)
        from datetime import timedelta
        cutoff = now - timedelta(days=30)
        qs = qs.filter(start_date__gte=cutoff)[:limit]

        total = qs.count()
        self.stdout.write(f'fetch_event_images: {total} candidates (limit {limit})')

        found = skipped = errors = 0

        for ev in qs:
            domain = urlparse(ev.website).netloc.lower()
            if domain in SKIP_DOMAINS:
                skipped += 1
                continue

            try:
                r = requests.get(
                    ev.website, timeout=10, headers=HEADERS,
                    allow_redirects=True,
                )
                r.raise_for_status()
                html = r.text
            except Exception as e:
                self.stderr.write(f'  [{ev.id}] fetch failed: {e}')
                errors += 1
                time.sleep(0.5)
                continue

            image_url = extract_og_image(html, ev.website)
            if not image_url:
                skipped += 1
                time.sleep(0.3)
                continue

            fname, content = download_image(image_url)
            if fname and content:
                ev.photo.save(fname, content, save=True)
                found += 1
                self.stdout.write(f'  [{ev.id}] ✓ {ev.title[:50]}')
            else:
                skipped += 1

            time.sleep(1)  # polite crawl rate

        self.stdout.write(
            self.style.SUCCESS(
                f'Done. {found} images saved, {skipped} skipped, {errors} errors.'
            )
        )
