"""
discover_instagram — find new PDX Instagram accounts to track and queue for review.

Two discovery strategies (Instagram search API now requires auth, so we work around it):

  1. Mine existing DB — scan descriptions, bios, notes, and website fields of
     Events, Venues, Artists, and PromoterProfiles for @handles and
     instagram.com/ URLs.  These are the most relevant accounts since they're
     already mentioned in your data.

  2. Scrape PDX event listing sites — fetch 19hz.info and similar pages and
     extract Instagram handles from the raw HTML.

Both strategies create InstagramAccount(status='pending') records for admin review.

Run:
    python manage.py discover_instagram
    python manage.py discover_instagram --strategy db          # DB mining only
    python manage.py discover_instagram --strategy web         # web scrape only
    python manage.py discover_instagram --dry-run
    python manage.py discover_instagram --min-followers 0      # no follower floor

Review results at /admin/events/instagramaccount/?status=pending
"""
import re
import time
import json
import urllib.request
import urllib.parse
from django.core.management.base import BaseCommand

# Instagram handle — letters, numbers, dot, underscore; 1-30 chars.
# Negative lookbehind on @ avoids matching email local-parts (user@domain.com).
# Negative lookahead on common TLDs drops website-style false positives.
HANDLE_RE = re.compile(
    r'(?:(?<!\w)@|instagram\.com/)([\w.]{2,30})'
    r'(?![.\w]*(\.com|\.org|\.net|\.io|\.edu|\.gov|\.co|\.us|\.info|\.biz))',
    re.I
)

# Pages to scrape for handles (HTML source is searched with HANDLE_RE)
SCRAPE_URLS = [
    ('19hz Oregon/PDX',  'https://19hz.info/eventlisting_ORE.php'),
    ('19hz Seattle/PNW', 'https://19hz.info/eventlisting_Seattle.php'),
]

# Handles to always skip (bots, platforms, generic accounts)
SKIP_HANDLES = {
    'instagram', 'facebook', 'twitter', 'youtube', 'tiktok', 'spotify',
    'bandcamp', 'soundcloud', 'mixcloud', 'beatport', 'discogs',
    'p', 'i', 'r', 'n', 's', 'a', 'c',  # single-letter junk
}

# Bio relevance filter applied when we have bio data (from profile fetch)
# Handles that pass the filter without bio data are still queued (bio is blank)
BIO_RELEVANCE = re.compile(
    r'\b(portland|pdx|p\.?d\.?x|oregon|pnw|pacific.?northwest|'
    r'rave|raves|techno|house|dnb|drum.?n.?bass|bass.?music|'
    r'electronic|edm|trance|dubstep|jungle|breakbeat|ambient|footwork|'
    r'dj|deejay|promoter|collective|crew|booking|'
    r'events?|nightlife|underground|warehouse|club|venue|'
    r'music|artist|producer|label|festival)\b',
    re.I
)

IG_PROFILE_API = 'https://i.instagram.com/api/v1/users/web_profile_info/'
IG_HDRS = {
    'User-Agent': (
        'Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/120.0.6099.230 Mobile Safari/537.36 '
        'Instagram/309.0.0.28.111'
    ),
    'X-IG-App-ID': '936619743392459',
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.9',
}
HTTP_HDRS = {'User-Agent': 'Mozilla/5.0 (compatible; CommunityPlaylist/1.0)'}
RATE_SLEEP = 7  # seconds between IG profile API calls


def _extract_handles(text):
    """Pull Instagram handles from free text."""
    found = set()
    for match in HANDLE_RE.finditer(text or ''):
        handle = match.group(1).lower().strip('._')
        if len(handle) >= 2 and handle not in SKIP_HANDLES:
            found.add(handle)
    return found


def _fetch_profile(handle):
    """Fetch IG profile dict or None. Raises RuntimeError on rate-limit."""
    params = urllib.parse.urlencode({'username': handle})
    req    = urllib.request.Request(f'{IG_PROFILE_API}?{params}', headers=IG_HDRS)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        return data.get('data', {}).get('user') or None
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RuntimeError('rate-limit (429)')
        if e.code == 404:
            return None
        raise
    except Exception as e:
        raise RuntimeError(str(e))


def _mine_db(known, stdout):
    """Strategy 1: scan existing model fields for @handles / instagram URLs."""
    from events.models import Event, Venue, Artist, PromoterProfile

    candidates = set()

    checks = [
        ('Event.description',         Event.objects.exclude(description='').values_list('description', flat=True)),
        ('Event.website',             Event.objects.exclude(website='').values_list('website', flat=True)),
        ('Venue.description',         Venue.objects.exclude(description='').values_list('description', flat=True)),
        ('Venue.instagram',           Venue.objects.exclude(instagram='').values_list('instagram', flat=True)),
        ('Artist.bio',                Artist.objects.exclude(bio='').values_list('bio', flat=True)),
        ('Artist.auto_bio',           Artist.objects.exclude(auto_bio='').values_list('auto_bio', flat=True)),
        ('Artist.instagram',          Artist.objects.exclude(instagram='').values_list('instagram', flat=True)),
        ('PromoterProfile.bio',       PromoterProfile.objects.exclude(bio='').values_list('bio', flat=True)),
        ('PromoterProfile.instagram', PromoterProfile.objects.exclude(instagram='').values_list('instagram', flat=True)),
    ]

    for label, qs in checks:
        found = set()
        for text in qs:
            found |= _extract_handles(text)
        new = found - known
        if new:
            stdout.write(f'    {label}: {len(new)} new handles')
        candidates |= new

    return candidates


def _mine_web(stdout):
    """Strategy 2: fetch PDX event listing pages and extract handles from HTML."""
    candidates = set()
    for name, url in SCRAPE_URLS:
        stdout.write(f'  Fetching {name}…', ending=' ')
        req = urllib.request.Request(url, headers=HTTP_HDRS)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                html = r.read().decode('utf-8', errors='replace')
            found = _extract_handles(html)
            stdout.write(f'{len(found)} handles found in page')
            candidates |= found
        except Exception as e:
            stdout.write(f'ERROR: {e}')
    return candidates


class Command(BaseCommand):
    help = 'Discover PDX Instagram accounts from existing data and web scraping.'

    def add_arguments(self, parser):
        parser.add_argument('--strategy', choices=['db', 'web', 'both'], default='both',
                            help='Which strategy to run (default: both)')
        parser.add_argument('--min-followers', type=int, default=100,
                            help='Skip accounts with fewer followers when profile is fetchable '
                                 '(default: 100; set 0 to disable)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print candidates without saving')
        parser.add_argument('--no-enrich', action='store_true',
                            help='Skip fetching IG profiles for bio/follower data (faster, less filtering)')

    def handle(self, *args, **options):
        from events.models import InstagramAccount

        strategy     = options['strategy']
        min_followers = options['min_followers']
        dry_run      = options['dry_run']
        no_enrich    = options['no_enrich']

        known = {
            h.lower().lstrip('@')
            for h in InstagramAccount.objects.values_list('handle', flat=True)
        }
        self.stdout.write(f'Known accounts: {len(known)}')

        raw_candidates = set()

        # ── Strategy 1: DB mining ─────────────────────────────────────────────
        if strategy in ('db', 'both'):
            self.stdout.write('\n-- Strategy 1: Mining existing DB fields --')
            raw_candidates |= _mine_db(known, self.stdout)

        # ── Strategy 2: Web scraping ──────────────────────────────────────────
        if strategy in ('web', 'both'):
            self.stdout.write('\n-- Strategy 2: Scraping PDX event pages --')
            raw_candidates |= _mine_web(self.stdout)

        # Remove already-known handles
        raw_candidates -= known
        self.stdout.write(f'\n{len(raw_candidates)} new handles to evaluate\n')

        queued = skipped = errors = 0

        for i, handle in enumerate(sorted(raw_candidates)):
            profile_data = {}
            display_name = ''
            bio          = ''
            followers    = None
            ig_user_id   = ''

            # Optionally fetch profile to apply follower floor + bio filter
            if not no_enrich:
                if i > 0:
                    time.sleep(RATE_SLEEP)
                self.stdout.write(f'  @{handle}…', ending=' ')
                try:
                    user = _fetch_profile(handle)
                except RuntimeError as e:
                    self.stdout.write(f'ERROR: {e}')
                    errors += 1
                    if '429' in str(e):
                        self.stdout.write('  Backing off 30s…')
                        time.sleep(30)
                    continue

                if user is None:
                    self.stdout.write('private / not found — skip')
                    skipped += 1
                    continue

                display_name = (user.get('full_name') or '').strip()
                bio          = (user.get('biography') or '').strip()
                followers    = (user.get('edge_followed_by') or {}).get('count')
                ig_user_id   = str(user.get('id') or '')

                # Follower floor
                if min_followers and (followers or 0) < min_followers:
                    self.stdout.write(f'only {followers} followers — skip')
                    skipped += 1
                    continue

                # Bio relevance check (only if bio is non-empty)
                if bio and not BIO_RELEVANCE.search(f'{bio} {display_name}'):
                    self.stdout.write(f'not relevant ({bio[:60]}) — skip')
                    skipped += 1
                    continue

                self.stdout.write(
                    f'✓ {display_name} | {followers:,} followers | {bio[:80]}'
                )
            else:
                self.stdout.write(f'  @{handle} [no enrich]')

            if dry_run:
                continue

            InstagramAccount.objects.get_or_create(
                handle=handle,
                defaults={
                    'ig_user_id':   ig_user_id,
                    'display_name': display_name,
                    'bio':          bio,
                    'follower_count': followers,
                    'status':       InstagramAccount.STATUS_PENDING,
                    'notes':        'Auto-discovered via discover_instagram',
                }
            )
            known.add(handle)
            queued += 1

        if dry_run:
            self.stdout.write(f'\n[dry-run] Would evaluate {len(raw_candidates)} handles.')
        else:
            self.stdout.write(
                f'\nDone. {queued} queued, {skipped} skipped, {errors} errors.'
            )
            if queued:
                self.stdout.write(
                    'Review at /admin/events/instagramaccount/?status=pending'
                )
