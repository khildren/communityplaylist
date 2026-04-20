"""
enrich_artists_beatport — find Beatport artist pages via the internal catalog API.

Beatport's old OAuth API (oauth-api.beatport.com/catalog/3/) is defunct.
Their website is Next.js (client-rendered), so the HTML search page is empty.
This command uses the internal catalog API that Beatport's frontend calls,
which returns JSON without auth for basic artist/label searches.

No API key required. Rate-limited to ~1 req/sec to avoid bans.

Covers both individual Artists and PromoterProfiles that are labels/collectives.

Run:
    python manage.py enrich_artists_beatport
    python manage.py enrich_artists_beatport --stubs-only
    python manage.py enrich_artists_beatport --name "Sullivan King"
    python manage.py enrich_artists_beatport --labels        # also enrich PromoterProfiles
    python manage.py enrich_artists_beatport --dry-run
"""
import time
import re
import urllib.request
import urllib.parse
import json
from django.core.management.base import BaseCommand

# Beatport's internal Next.js API — no auth required for catalog search
BP_API_BASE = 'https://www.beatport.com/api/v4/catalog'
BP_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Referer': 'https://www.beatport.com/',
}


def _bp_get(endpoint, params):
    """GET from the Beatport internal catalog API. Returns parsed JSON or None."""
    url = f'{BP_API_BASE}/{endpoint}/?{urllib.parse.urlencode(params)}'
    req = urllib.request.Request(url, headers=BP_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _search_artist(name):
    """
    Search Beatport for an artist by name.
    Returns dict with keys: url, slug, id, name — or None.
    """
    data = _bp_get('artists', {'q': name, 'per_page': 10})
    if not data:
        return None

    results = data.get('results', [])
    if not results:
        return None

    lower = name.lower()
    # 1. Exact name match
    exact = [r for r in results if r.get('name', '').lower() == lower]
    if exact:
        r = exact[0]
    else:
        # 2. First result (best-guess)
        r = results[0]

    slug = r.get('slug', '')
    bp_id = r.get('id', '')
    return {
        'url':  f'https://www.beatport.com/artist/{slug}/{bp_id}',
        'slug': slug,
        'id':   str(bp_id),
        'name': r.get('name', ''),
    }


def _search_label(name):
    """
    Search Beatport for a label/collective by name.
    Returns dict with keys: url, slug, id, name — or None.
    """
    data = _bp_get('labels', {'q': name, 'per_page': 10})
    if not data:
        return None

    results = data.get('results', [])
    if not results:
        return None

    lower = name.lower()
    exact = [r for r in results if r.get('name', '').lower() == lower]
    r = exact[0] if exact else results[0]

    slug = r.get('slug', '')
    bp_id = r.get('id', '')
    return {
        'url':  f'https://www.beatport.com/label/{slug}/{bp_id}',
        'slug': slug,
        'id':   str(bp_id),
        'name': r.get('name', ''),
    }


class Command(BaseCommand):
    help = 'Enrich Artist (and optionally PromoterProfile) records with Beatport URLs.'

    def add_arguments(self, parser):
        parser.add_argument('--stubs-only', action='store_true',
                            help='Only process is_stub=True artists')
        parser.add_argument('--force', action='store_true',
                            help='Re-fetch even if record already has a Beatport URL')
        parser.add_argument('--name', type=str, default='',
                            help='Enrich a single artist by name')
        parser.add_argument('--labels', action='store_true',
                            help='Also search Beatport labels for PromoterProfiles')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print results without saving')

    def handle(self, *args, **options):
        from events.models import Artist, PromoterProfile
        from django.utils import timezone

        stubs_only  = options['stubs_only']
        force       = options['force']
        dry_run     = options['dry_run']
        do_labels   = options['labels']
        name_filter = options['name'].strip()

        # ── Artists ──────────────────────────────────────────────────────────
        qs = Artist.objects.all()
        if name_filter:
            qs = qs.filter(name__iexact=name_filter)
        elif stubs_only:
            qs = qs.filter(is_stub=True)
        if not force:
            qs = qs.filter(beatport='')

        total = qs.count()
        self.stdout.write(f'Searching Beatport artists for {total} records…')

        found = skipped = errors = 0

        for artist in qs:
            try:
                match = _search_artist(artist.name)
            except Exception as e:
                self.stderr.write(f'  ERROR {artist.name}: {e}')
                errors += 1
                time.sleep(2)
                continue

            if not match:
                self.stdout.write(f'  — not found: {artist.name}')
                skipped += 1
                time.sleep(1.1)
                continue

            self.stdout.write(
                f'  ✓ {artist.name} → {match["url"]}  (bp_name="{match["name"]}")'
            )

            if not dry_run:
                artist.beatport = match['url']
                artist.last_enriched_at = timezone.now()
                artist.save(update_fields=['beatport', 'last_enriched_at'])

            found += 1
            time.sleep(1.1)

        self.stdout.write(
            f'\nArtists: {found} found, {skipped} not on Beatport, {errors} errors.'
        )

        # ── PromoterProfiles (labels / collectives) ───────────────────────
        if not do_labels:
            return

        pqs = PromoterProfile.objects.all()
        if name_filter:
            pqs = pqs.filter(name__iexact=name_filter)
        if not force:
            pqs = pqs.filter(beatport='') if hasattr(PromoterProfile, 'beatport') else pqs.none()

        ptotal = pqs.count()
        self.stdout.write(f'\nSearching Beatport labels for {ptotal} PromoterProfiles…')

        pfound = pskipped = perrors = 0

        for prof in pqs:
            try:
                match = _search_label(prof.name)
            except Exception as e:
                self.stderr.write(f'  ERROR {prof.name}: {e}')
                perrors += 1
                time.sleep(2)
                continue

            if not match:
                self.stdout.write(f'  — not found: {prof.name}')
                pskipped += 1
                time.sleep(1.1)
                continue

            self.stdout.write(
                f'  ✓ {prof.name} → {match["url"]}  (bp_name="{match["name"]}")'
            )
            pfound += 1
            time.sleep(1.1)

        self.stdout.write(
            f'Labels: {pfound} found, {pskipped} not on Beatport, {perrors} errors.'
        )
