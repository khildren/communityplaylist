"""
enrich_instagram — pull bio, external links, and profile photo from public
Instagram profiles for both Artist and PromoterProfile records.

Uses Instagram's internal profile API (no auth required, rate-limited to ~1/5s).
Fills: bio/auto_bio supplement, website (external_url from bio), youtube link
       if present in bio_links, plus downloads profile photo if missing.

Run:
    python manage.py enrich_instagram
    python manage.py enrich_instagram --model artist
    python manage.py enrich_instagram --model crew
    python manage.py enrich_instagram --handle gnosisdnb
    python manage.py enrich_instagram --dry-run
    python manage.py enrich_instagram --force    # re-fetch even if bio already set
"""
import time
import re
import json
import urllib.request
import urllib.parse
from django.core.management.base import BaseCommand

IG_API  = 'https://i.instagram.com/api/v1/users/web_profile_info/'
IG_HDRS = {
    # Mobile UA avoids some desktop-targeted blocks
    'User-Agent': (
        'Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/120.0.6099.230 Mobile Safari/537.36 '
        'Instagram/309.0.0.28.111'
    ),
    'X-IG-App-ID': '936619743392459',
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.9',
}
RATE_SLEEP = 6  # seconds between requests — IG rate-limits aggressively


def _fetch_profile(handle):
    """Return Instagram user dict or None. Raises on hard HTTP errors."""
    params  = urllib.parse.urlencode({'username': handle.lstrip('@')})
    req     = urllib.request.Request(f'{IG_API}?{params}', headers=IG_HDRS)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RuntimeError('Instagram rate-limit (429) — wait and retry')
        if e.code == 404:
            return None
        raise
    except Exception as e:
        raise RuntimeError(str(e))

    return data.get('data', {}).get('user') or None


def _extract_links(user):
    """Parse bio_links and external_url into a dict of platform → value."""
    links = {}
    all_urls = [u.get('url', '') for u in (user.get('bio_links') or [])]
    ext = user.get('external_url', '')
    if ext:
        all_urls.append(ext)

    for url in all_urls:
        url = url.strip()
        if not url:
            continue
        if 'youtube.com/' in url or 'youtu.be' in url:
            links.setdefault('youtube', url)
        elif 'soundcloud.com/' in url:
            links.setdefault('soundcloud', url.split('soundcloud.com/')[-1].strip('/'))
        elif 'bandcamp.com' in url:
            links.setdefault('bandcamp', url)
        elif 'spotify.com/artist/' in url:
            links.setdefault('spotify', url)
        elif 'mixcloud.com/' in url:
            links.setdefault('mixcloud', url.split('mixcloud.com/')[-1].strip('/'))
        elif 'beatport.com/' in url:
            links.setdefault('beatport', url)
        elif 'discogs.com/' in url:
            links.setdefault('discogs', url)
        else:
            links.setdefault('website', url)

    return links


def _apply_to_record(obj, user, dry_run, stdout):
    """Write enriched fields onto an Artist or PromoterProfile. Returns list of changed fields."""
    bio     = (user.get('biography') or '').strip()
    pic_url = user.get('profile_pic_url_hd') or user.get('profile_pic_url') or ''
    name    = user.get('full_name', '').strip()
    followers = (user.get('edge_followed_by') or {}).get('count')
    links   = _extract_links(user)

    changed = []

    # ── Bio ──────────────────────────────────────────────────────────────────
    is_artist = hasattr(obj, 'auto_bio')   # Artist has auto_bio; PromoterProfile doesn't

    if bio and len(bio) > 10:
        if is_artist:
            if not obj.bio and 'Instagram:' not in (obj.auto_bio or ''):
                new_auto = (obj.auto_bio or '').rstrip() + f'\n\nFrom Instagram: {bio}'
                obj.auto_bio = new_auto.lstrip()
                changed.append('auto_bio')
        else:
            if not obj.bio:
                obj.bio = bio
                changed.append('bio')

    # ── Platform links ────────────────────────────────────────────────────────
    for field, val in links.items():
        if field == 'website' and not getattr(obj, 'website', ''):
            obj.website = val
            changed.append('website')
        elif field in ('youtube', 'soundcloud', 'bandcamp', 'spotify',
                       'mixcloud', 'beatport', 'discogs'):
            if hasattr(obj, field) and not getattr(obj, field):
                setattr(obj, field, val)
                changed.append(field)

    # ── Profile photo ─────────────────────────────────────────────────────────
    if pic_url and not obj.photo:
        from django.core.files.base import ContentFile
        import urllib.request as _ur
        try:
            with _ur.urlopen(_ur.Request(pic_url, headers={'User-Agent': IG_HDRS['User-Agent']}), timeout=15) as img_r:
                img_bytes = img_r.read()
            ext = '.jpg'
            fname = f'ig_{obj.instagram.lstrip("@")}{ext}'
            obj.photo.save(fname, ContentFile(img_bytes), save=False)
            changed.append('photo')
        except Exception as e:
            stdout.write(f'    photo download failed: {e}')

    stdout.write(
        f'  ✓ @{obj.instagram} | bio={bool(bio)} | followers={followers} | '
        f'links={list(links.keys())} | photo={"new" if "photo" in changed else "skip"}'
    )
    if bio:
        stdout.write(f'    bio: {bio[:120]}')

    return changed


class Command(BaseCommand):
    help = 'Enrich Artist / PromoterProfile records from public Instagram profiles.'

    def add_arguments(self, parser):
        parser.add_argument('--model', choices=['artist', 'crew', 'both'], default='both',
                            help='Which model(s) to enrich (default: both)')
        parser.add_argument('--handle', type=str, default='',
                            help='Enrich a single record by Instagram handle')
        parser.add_argument('--force', action='store_true',
                            help='Re-fetch even if bio/links already populated')
        parser.add_argument('--dry-run', action='store_true',
                            help='Fetch data and print without saving')

    def handle(self, *args, **options):
        from events.models import Artist, PromoterProfile
        from django.utils import timezone

        model   = options['model']
        handle  = options['handle'].lstrip('@').strip()
        force   = options['force']
        dry_run = options['dry_run']

        records = []

        if model in ('artist', 'both'):
            qs = Artist.objects.exclude(instagram='')
            if handle:
                qs = qs.filter(instagram__iexact=handle)
            if not force:
                qs = qs.filter(bio='', auto_bio='') | Artist.objects.exclude(instagram='').filter(
                    photo__exact='', **({'instagram__iexact': handle} if handle else {})
                )
                # Simplify: re-query for no-bio OR no-photo, not-yet-enriched
                qs = Artist.objects.exclude(instagram='')
                if handle:
                    qs = qs.filter(instagram__iexact=handle)
                if not force:
                    from django.db.models import Q
                    qs = qs.filter(Q(bio='') | Q(photo=''))
            records += [('artist', a) for a in qs]

        if model in ('crew', 'both'):
            qs = PromoterProfile.objects.exclude(instagram='')
            if handle:
                qs = qs.filter(instagram__iexact=handle)
            if not force:
                from django.db.models import Q
                qs = qs.filter(Q(bio='') | Q(photo=''))
            records += [('crew', p) for p in qs]

        # Deduplicate: same handle appearing in both Artist + PromoterProfile
        seen_handles = set()
        deduped = []
        for kind, obj in records:
            h = (obj.instagram or '').lower().lstrip('@')
            if h and h not in seen_handles:
                seen_handles.add(h)
                deduped.append((kind, obj))
        records = deduped

        self.stdout.write(f'Fetching Instagram profiles for {len(records)} records…')

        enriched = skipped = errors = 0

        for i, (kind, obj) in enumerate(records):
            if i > 0:
                time.sleep(RATE_SLEEP)

            ig_handle = (obj.instagram or '').lstrip('@')
            self.stdout.write(f'  [{kind}] @{ig_handle} ({obj.name})')

            try:
                user = _fetch_profile(ig_handle)
            except RuntimeError as e:
                self.stderr.write(f'    ERROR: {e}')
                errors += 1
                time.sleep(15)  # back off on rate limit
                continue
            except Exception as e:
                self.stderr.write(f'    ERROR: {e}')
                errors += 1
                continue

            if not user:
                self.stdout.write(f'    — profile not found or private')
                skipped += 1
                continue

            changed = _apply_to_record(obj, user, dry_run, self.stdout)

            if not dry_run and changed:
                if hasattr(obj, 'last_enriched_at'):
                    obj.last_enriched_at = timezone.now()
                    if 'last_enriched_at' not in changed:
                        changed.append('last_enriched_at')
                obj.save(update_fields=changed)

            enriched += 1

        self.stdout.write(
            f'\nDone. {enriched} enriched, {skipped} not found/private, {errors} errors.'
        )
