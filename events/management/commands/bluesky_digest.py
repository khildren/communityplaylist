"""
management command: bluesky_digest

Posts today's Portland events to Bluesky as a threaded digest.
- Up to SOCIAL_DAILY_POST_LIMIT events: one thread (header + per-event posts)
- Over the limit: splits into per-category threads, each with a link to
  the pre-filtered homepage (e.g. /?cat=music)

Each event post includes:
  - Title, time, venue hashtag, title hashtags
  - Direct link to event page
  - Venue @mention if their Bluesky handle is in the Venue record

Run daily via cron (example: 8 AM):
    0 8 * * * docker exec cp-communityplaylist-1 python manage.py bluesky_digest
"""
import time
import requests
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from events.models import Event, Venue
from board.social import (
    _bsky_session, _bsky_create, _bsky_facets,
    build_event_batch_posts, _bsky_upload_blob,
    CP_BASE,
)

BSKY = 'https://bsky.social/xrpc'


def resolve_handle(handle):
    handle = handle.lstrip('@')
    try:
        r = requests.get(f'{BSKY}/com.atproto.identity.resolveHandle',
                         params={'handle': handle}, timeout=5)
        return r.json().get('did') if r.ok else None
    except Exception:
        return None


class Command(BaseCommand):
    help = "Post today's PDX events to Bluesky (splits by category when > limit)"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit   = getattr(settings, 'SOCIAL_DAILY_POST_LIMIT', 27)

        token, did = _bsky_session()
        if not token:
            self.stderr.write('[bluesky_digest] no credentials — set BLUESKY_HANDLE + BLUESKY_APP_PASSWORD')
            return

        now         = timezone.now()
        today_start = now.replace(hour=0,  minute=0,  second=0,  microsecond=0)
        today_end   = now.replace(hour=23, minute=59, second=59, microsecond=0)
        events = Event.objects.prefetch_related('genres', 'promoters').filter(
            status='approved',
            start_date__gte=today_start,
            start_date__lte=today_end,
        ).order_by('start_date')

        if not events.exists():
            url = CP_BASE
            text = f'📅 No events today — check upcoming at {url}\n\n#PDXEvents #Portland'
            if not dry_run:
                _bsky_create(token, did, text,
                             facets=_bsky_facets(text, links=[url], hashtags=['#PDXEvents', '#Portland']))
            else:
                self.stdout.write(f'[DRY] no-events post')
            return

        # Build venue handle → DID cache
        venue_handles = {
            v.name.split()[0].lower(): v.bluesky.lstrip('@')
            for v in Venue.objects.exclude(bluesky='')
        }
        did_cache = {}

        def get_venue_did(location):
            first = location.split(',')[0].strip().lower()
            for key, handle in venue_handles.items():
                if key in first:
                    if handle not in did_cache:
                        did_cache[handle] = resolve_handle(handle)
                    return handle, did_cache.get(handle)
            return None, None

        # Get batch structure from social module
        batches = build_event_batch_posts(events, daily_limit=limit)

        total_posted = 0
        for (header_text, batch_link, event_texts) in batches:
            if dry_run:
                self.stdout.write(f'\n[DRY] HEADER: {header_text[:80]}')
                for t, _, _ in event_texts:
                    self.stdout.write(f'  [DRY] {t[:60]}')
                continue

            # Post the header
            htags = ['#PDXEvents', '#Portland', '#PDX']
            hfacets = _bsky_facets(header_text, links=[batch_link], hashtags=htags)
            h_uri, h_cid = _bsky_create(token, did, header_text, facets=hfacets)
            self.stdout.write(f'  → header posted: {h_uri}')
            time.sleep(2)

            # Thread root ref
            root_ref = reply_ref = {
                'root':   {'uri': h_uri, 'cid': h_cid},
                'parent': {'uri': h_uri, 'cid': h_cid},
            }

            # Post each event as a reply in the thread
            for (text, eurl, tag_list) in event_texts:
                # Check for venue @mention
                e_slug = eurl.rstrip('/').split('/')[-1]
                try:
                    event = Event.objects.get(slug=e_slug)
                    v_handle, v_did = get_venue_did(event.location)
                except Event.DoesNotExist:
                    v_handle, v_did = None, None

                facet_links    = [eurl]
                facet_hashtags = [t for t in tag_list if t.startswith('#')]

                facets = _bsky_facets(text, links=facet_links, hashtags=facet_hashtags)

                # Add venue @mention facet if we have a DID
                if v_handle and v_did:
                    tb = text.encode('utf-8')
                    mention_str = f'@{v_handle}'
                    mb = mention_str.encode('utf-8')
                    idx = tb.find(mb)
                    if idx >= 0:
                        if facets is None:
                            facets = []
                        facets.append({
                            '$type': 'app.bsky.richtext.facet',
                            'index': {'byteStart': idx, 'byteEnd': idx + len(mb)},
                            'features': [{'$type': 'app.bsky.richtext.facet#mention', 'did': v_did}],
                        })

                uri, cid = _bsky_create(token, did, text,
                                        facets=facets, reply_ref=reply_ref)
                reply_ref = {
                    'root':   root_ref['root'],
                    'parent': {'uri': uri, 'cid': cid},
                }
                total_posted += 1
                time.sleep(2)

            self.stdout.write(f'  ✓ batch posted ({len(event_texts)} events)')
            if len(batches) > 1:
                time.sleep(5)  # pause between category batches

        self.stdout.write(f'[bluesky_digest] done — {total_posted} event posts')
