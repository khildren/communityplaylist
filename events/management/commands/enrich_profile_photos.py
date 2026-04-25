"""
enrich_profile_photos — Fetch missing or broken photos for Artist, Promoter, and Venue profiles.

Priority order for each profile:
  1. Instagram profile_pic_url (if handle is set) via the session-based API
  2. og:image from the profile's website
  3. Skip (log as unfetchable)

Runs on profiles that have no photo field set (or whose photo file is missing
on disk — run check_media_files --fix-dangling first to clear broken refs).

Usage:
  python manage.py enrich_profile_photos
  python manage.py enrich_profile_photos --model artist
  python manage.py enrich_profile_photos --limit 50
  python manage.py enrich_profile_photos --dry-run
"""
import mimetypes
import os
import time

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from events.models import Artist, PromoterProfile, Venue
from events.management.commands.fetch_event_images import (
    extract_og_image,
    download_image,
    HEADERS,
)

try:
    from events.management.commands.enrich_instagram import _fetch_profile as _ig_fetch_profile
    HAS_IG = True
except (ImportError, AttributeError):
    HAS_IG = False
    _ig_fetch_profile = None

MEDIA_ROOT = str(settings.MEDIA_ROOT)


def _photo_exists(obj, field='photo'):
    """True if the photo/logo field is set AND file exists on disk."""
    f = getattr(obj, field, None)
    if not f:
        return False
    return os.path.exists(os.path.join(MEDIA_ROOT, str(f)))


def _fetch_from_instagram(handle, upload_to, label, stdout):
    """Try to get a profile pic from Instagram. Returns (fname, ContentFile) or (None, None)."""
    if not HAS_IG or not handle:
        return None, None
    try:
        user = _ig_fetch_profile(handle.lstrip('@'))
        if not user:
            return None, None
        pic_url = user.get('profile_pic_url_hd') or user.get('profile_pic_url')
        if not pic_url:
            return None, None
        r = requests.get(pic_url, timeout=15, headers=HEADERS)
        r.raise_for_status()
        ct = r.headers.get('content-type', '').split(';')[0].strip().lower()
        ext = mimetypes.guess_extension(ct) or '.jpg'
        ext = ext.replace('.jpe', '.jpg')
        fname = f'ig_{slugify(handle)}{ext}'
        stdout.write(f'    ↳ Instagram profile pic: {pic_url[:60]}')
        return fname, ContentFile(r.content)
    except Exception as e:
        stdout.write(f'    ↳ Instagram failed: {e}')
        return None, None


def _fetch_from_website(website, label, stdout):
    """Try og:image from the profile website. Returns (fname, ContentFile) or (None, None)."""
    if not website:
        return None, None
    try:
        r = requests.get(website, timeout=12, headers=HEADERS, allow_redirects=True)
        if r.status_code == 403:
            return None, None
        r.raise_for_status()
        img_url = extract_og_image(r.text, website)
        if not img_url:
            return None, None
        fname, content = download_image(img_url)
        if fname:
            stdout.write(f'    ↳ og:image: {img_url[:60]}')
        return fname, content
    except Exception as e:
        stdout.write(f'    ↳ website fetch failed: {e}')
        return None, None


def _enrich(obj, photo_field, upload_to, dry_run, stdout):
    """Attempt to fill the photo/logo field. Returns True if a photo was saved."""
    name  = str(obj)[:50]
    handle  = getattr(obj, 'instagram', '')
    website = getattr(obj, 'website', '')

    stdout.write(f'  {name}')

    fname = content = None

    # Try Instagram first
    if handle and HAS_IG:
        fname, content = _fetch_from_instagram(handle, upload_to, name, stdout)
        time.sleep(1.0)

    # Fall back to website og:image
    if not content and website:
        fname, content = _fetch_from_website(website, name, stdout)
        time.sleep(0.5)

    if not content:
        stdout.write('    ↳ no photo source found — skip')
        return False

    if dry_run:
        stdout.write(f'    ↳ [DRY RUN] would save {fname}')
        return True

    photo_fld = getattr(obj, photo_field)
    photo_fld.save(fname, content, save=True)
    stdout.write(f'    ↳ saved {fname}')
    return True


class Command(BaseCommand):
    help = 'Fetch missing profile photos from Instagram or og:image for Artist/Promoter/Venue'

    def add_arguments(self, parser):
        parser.add_argument('--model', choices=['artist', 'promoter', 'venue', 'all'],
                            default='all')
        parser.add_argument('--limit', type=int, default=30,
                            help='Max profiles to process per run (default 30)')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        model   = options['model']
        limit   = options['limit']
        dry_run = options['dry_run']

        saved = skipped = 0
        budget = limit

        if model in ('artist', 'all') and budget > 0:
            self.stdout.write(self.style.MIGRATE_HEADING('Artist Photos'))
            qs = Artist.objects.filter(
                is_stub=False, photo=''
            ).exclude(instagram='', website='').order_by('name')[:budget]
            for a in qs:
                if not _photo_exists(a, 'photo'):
                    ok = _enrich(a, 'photo', 'artists/', dry_run, self.stdout)
                    saved += ok; skipped += not ok
                    budget -= 1
            self.stdout.write(f'  Artists: {saved} saved, {skipped} skipped')

        if model in ('promoter', 'all') and budget > 0:
            self.stdout.write(self.style.MIGRATE_HEADING('Promoter Photos'))
            p_saved = p_skip = 0
            qs = PromoterProfile.objects.filter(
                is_public=True, photo=''
            ).exclude(instagram='', website='').order_by('name')[:budget]
            for p in qs:
                if not _photo_exists(p, 'photo'):
                    ok = _enrich(p, 'photo', 'promoters/', dry_run, self.stdout)
                    p_saved += ok; p_skip += not ok
                    budget -= 1
            saved += p_saved; skipped += p_skip
            self.stdout.write(f'  Promoters: {p_saved} saved, {p_skip} skipped')

        if model in ('venue', 'all') and budget > 0:
            self.stdout.write(self.style.MIGRATE_HEADING('Venue Logos'))
            v_saved = v_skip = 0
            qs = Venue.objects.filter(
                active=True, logo=''
            ).exclude(website='').order_by('name')[:budget]
            for v in qs:
                if not _photo_exists(v, 'logo'):
                    ok = _enrich(v, 'logo', 'venues/', dry_run, self.stdout)
                    v_saved += ok; v_skip += not ok
                    budget -= 1
            saved += v_saved; skipped += v_skip
            self.stdout.write(f'  Venues: {v_saved} saved, {v_skip} skipped')

        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write('')
        self.stdout.write(
            self.style.SUCCESS(f'{prefix}Photos fetched: {saved}  |  Skipped: {skipped}')
        )
