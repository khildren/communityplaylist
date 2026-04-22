import re, unicodedata, requests, time
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.timezone import localtime
from django.conf import settings
from events.models import Event, Venue

BSKY = 'https://bsky.social/xrpc'
MAX_EVENTS = 12

def location_hashtag(location):
    """'Living Häus Beer Co, SE Hawthorne' → '#LivingHausBeerCo'"""
    name = location.split(',')[0].strip()
    # Normalize unicode (ä→a, ü→u, etc.)
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
    name = re.sub(r'[^a-zA-Z0-9 ]', '', name)
    return '#' + ''.join(w.capitalize() for w in name.split())

def resolve_handle(handle):
    """Return DID for a Bluesky handle, or None on failure."""
    handle = handle.lstrip('@')
    try:
        r = requests.get(f'{BSKY}/com.atproto.identity.resolveHandle',
                         params={'handle': handle}, timeout=5)
        return r.json().get('did') if r.ok else None
    except Exception:
        return None

class Command(BaseCommand):
    help = "Post today's events to Bluesky"

    def handle(self, *a, **k):
        auth = requests.post(f'{BSKY}/com.atproto.server.createSession', json={
            'identifier': settings.BLUESKY_HANDLE,
            'password':   settings.BLUESKY_APP_PASSWORD,
        }).json()
        did     = auth['did']
        headers = {'Authorization': f'Bearer {auth["accessJwt"]}'}

        # Cache handle→DID lookups within this run
        did_cache = {}

        def get_did(handle):
            handle = handle.lstrip('@')
            if handle not in did_cache:
                did_cache[handle] = resolve_handle(handle)
            return did_cache[handle]

        now         = timezone.now()
        today_start = now.replace(hour=0,  minute=0,  second=0,  microsecond=0)
        today_end   = now.replace(hour=23, minute=59, second=59, microsecond=0)
        events = Event.objects.prefetch_related('promoters', 'genres').filter(
            status='approved',
            start_date__gte=today_start,
            start_date__lte=today_end,
        ).order_by('start_date')

        # Build venue handle map: first word of venue name → bluesky handle
        venue_handles = {
            v.name.split()[0].lower(): v.bluesky
            for v in Venue.objects.exclude(bluesky='')
        }

        def venue_handle_for(location):
            first = location.split(',')[0].strip().lower()
            for key, handle in venue_handles.items():
                if key in first:
                    return handle
            return None

        def build_facets(text_bytes, items):
            """items = list of (substring, uri_or_None, did_or_None)"""
            facets = []
            for substr, uri, mention_did in items:
                b = substr.encode('utf-8')
                idx = text_bytes.find(b)
                if idx < 0:
                    continue
                end = idx + len(b)
                if uri:
                    facets.append({
                        '$type': 'app.bsky.richtext.facet',
                        'index': {'byteStart': idx, 'byteEnd': end},
                        'features': [{'$type': 'app.bsky.richtext.facet#link', 'uri': uri}],
                    })
                elif mention_did:
                    facets.append({
                        '$type': 'app.bsky.richtext.facet',
                        'index': {'byteStart': idx, 'byteEnd': end},
                        'features': [{'$type': 'app.bsky.richtext.facet#mention', 'did': mention_did}],
                    })
            return facets or None

        def bsky_post(text, facet_items=None):
            tb = text.encode('utf-8')
            record = {
                '$type':     'app.bsky.feed.post',
                'text':      text,
                'createdAt': timezone.now().strftime('%Y-%m-%dT%H:%M:%S.000Z'),
                'langs':     ['en-US'],
            }
            if facet_items:
                fs = build_facets(tb, facet_items)
                if fs:
                    record['facets'] = fs
            return requests.post(f'{BSKY}/com.atproto.repo.createRecord', headers=headers, json={
                'repo': did, 'collection': 'app.bsky.feed.post', 'record': record,
            })

        date_str = localtime(now).strftime('%A, %B %d')
        cp_url   = 'https://communityplaylist.com'

        if not events.exists():
            bsky_post(f'📅 No events today — check upcoming at {cp_url}',
                      [(cp_url, cp_url, None)])
            return

        bsky_post(f"🌹 Today's PDX Events — {date_str}\n{cp_url}",
                  [(cp_url, cp_url, None)])
        time.sleep(2)

        for e in events[:MAX_EVENTS]:
            genres = ', '.join(e.genres.values_list('name', flat=True)) or 'various'
            cost   = 'FREE' if e.is_free else (e.price_info or 'Paid')
            url    = f'{cp_url}/events/{e.slug}/'

            # Venue: tag if handle known, else hashtag
            v_handle = venue_handle_for(e.location)
            if v_handle:
                v_did    = get_did(v_handle)
                at_venue = f'@{v_handle.lstrip("@")}'
                venue_str = at_venue
            else:
                v_did    = None
                at_venue = None
                venue_str = location_hashtag(e.location)

            # Promoters with Bluesky handles
            promo_tags = []
            for p in e.promoters.exclude(bluesky=''):
                p_did = get_did(p.bluesky)
                if p_did:
                    promo_tags.append((f'@{p.bluesky.lstrip("@")}', p_did))

            promo_str = '  '.join(t for t, _ in promo_tags)

            location_line = e.location[:50]
            text = (
                f"{e.title}\n"
                f"📅 {localtime(e.start_date).strftime('%I:%M %p')}  📍 {location_line}\n"
                f"🎵 {genres[:35]}  {cost}\n"
                f"{venue_str}"
                + (f"  {promo_str}" if promo_str else '')
                + f"\n{url}"
            )
            if len(text) > 300:
                text = text[:297] + '...'

            facet_items = [(url, url, None)]
            if at_venue and v_did:
                facet_items.append((at_venue, None, v_did))
            for tag, p_did in promo_tags:
                facet_items.append((tag, None, p_did))

            bsky_post(text, facet_items)
            time.sleep(2)

        self.stdout.write(f"Posted {min(events.count(), MAX_EVENTS)} events to Bluesky")
