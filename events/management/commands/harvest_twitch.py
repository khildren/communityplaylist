"""
harvest_twitch — Pull VODs and live stream status from Twitch channels
connected to artists, venues, and promoters.

Twitch Helix API uses OAuth2 client_credentials (app access token).
Token lifetime is ~60 days; we re-request on each run (it's a single fast call).

What this does per connected channel:
  1. Resolves username → user ID + display name
  2. Checks if currently live (GET /helix/streams)
     - If live: creates/updates a VideoTrack with source_type='twitch_live', is_live=True
     - If not live: marks any existing live entry as is_live=False
  3. Fetches recent VODs (GET /helix/videos?type=archive, first=max)
     - Stores each as VideoTrack with source_type='twitch_vod'

Usage:
  python manage.py harvest_twitch              # all sources
  python manage.py harvest_twitch --max 10    # limit VODs per channel
  python manage.py harvest_twitch --live-only # only update live status (fast)
  python manage.py harvest_twitch --dry-run   # print plan, no DB writes
"""

import re
import time
import logging
from datetime import datetime, timezone as dt_tz

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from events.models import Artist, PromoterProfile, Venue, VideoTrack

logger = logging.getLogger(__name__)

TWITCH_AUTH = 'https://id.twitch.tv/oauth2/token'
TWITCH_API  = 'https://api.twitch.tv/helix'


def get_access_token(client_id, client_secret):
    """Request a fresh app access token via client_credentials flow."""
    r = requests.post(TWITCH_AUTH, data={
        'client_id':     client_id,
        'client_secret': client_secret,
        'grant_type':    'client_credentials',
    }, timeout=10)
    r.raise_for_status()
    return r.json()['access_token']


class TwitchUserNotFound(Exception):
    """Raised when Twitch returns 400 for a username — channel doesn't exist."""


def _get(endpoint, params, client_id, token):
    """Twitch Helix API GET with auth headers."""
    headers = {
        'Client-Id':     client_id,
        'Authorization': f'Bearer {token}',
    }
    r = requests.get(f'{TWITCH_API}/{endpoint}', params=params,
                     headers=headers, timeout=15)
    if r.status_code == 400:
        raise TwitchUserNotFound(params.get('user_login', ''))
    r.raise_for_status()
    return r.json()


def resolve_user(username, client_id, token):
    """Return (user_id, display_name, profile_image_url) or (None, None, None)."""
    data = _get('users', {'login': username.lower().lstrip('@')}, client_id, token)
    items = data.get('data', [])
    if not items:
        return None, None, None
    u = items[0]
    return u['id'], u['display_name'], u.get('profile_image_url', '')


def check_live(username, client_id, token):
    """
    Return live stream info dict or None if offline.
    Dict has: title, viewer_count, thumbnail_url, started_at
    """
    data = _get('streams', {'user_login': username.lower()}, client_id, token)
    items = data.get('data', [])
    if not items:
        return None
    s = items[0]
    # Twitch thumbnail URLs have {width}x{height} placeholders
    thumb = s.get('thumbnail_url', '').replace('{width}', '640').replace('{height}', '360')
    return {
        'title':        s.get('title', ''),
        'viewer_count': s.get('viewer_count', 0),
        'thumbnail_url': thumb,
        'started_at':   s.get('started_at', ''),
        'game_name':    s.get('game_name', ''),
    }


def get_vods(user_id, max_vods, client_id, token):
    """Fetch up to max_vods archived VODs for a Twitch user. Returns list of dicts."""
    vods = []
    cursor = None
    while len(vods) < max_vods:
        params = {
            'user_id': user_id,
            'type':    'archive',   # past broadcasts only (not highlights/uploads)
            'first':   min(20, max_vods - len(vods)),
        }
        if cursor:
            params['after'] = cursor
        data = _get('videos', params, client_id, token)
        items = data.get('data', [])
        if not items:
            break
        vods.extend(items)
        cursor = data.get('pagination', {}).get('cursor')
        if not cursor:
            break
    return vods[:max_vods]


def parse_duration(duration_str):
    """Convert Twitch duration string '3h12m45s' → seconds."""
    m = re.match(r'(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?', duration_str or '')
    if not m:
        return None
    h, mn, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mn * 60 + s


class Command(BaseCommand):
    help = 'Harvest Twitch VODs and live stream status from connected channels'

    def add_arguments(self, parser):
        parser.add_argument('--max', type=int, default=10,
                            help='Max VODs to fetch per channel (default 10)')
        parser.add_argument('--live-only', action='store_true',
                            help='Only update live status, skip VOD harvest')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print plan without writing to DB')
        parser.add_argument('--source', choices=['artists', 'venues', 'promoters', 'all'],
                            default='all')

    def handle(self, *args, **options):
        client_id = getattr(settings, 'TWITCH_CLIENT_ID', '')
        client_secret = getattr(settings, 'TWITCH_CLIENT_SECRET', '')
        if not client_id or not client_secret:
            raise CommandError(
                'TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET are not set.\n'
                'Register your app at: dev.twitch.tv → Your Console → Applications\n'
                'Then add both values to .env'
            )

        self.stdout.write('Getting Twitch access token…')
        token = get_access_token(client_id, client_secret)
        self.stdout.write(self.style.SUCCESS('Token OK'))

        max_vods  = options['max']
        live_only = options['live_only']
        dry_run   = options['dry_run']
        source    = options['source']

        total_created = total_updated = total_skipped = 0
        total_live = 0

        def process(qs, label):
            nonlocal total_created, total_updated, total_skipped, total_live

            for obj in qs:
                username = getattr(obj, 'twitch', '').strip().lstrip('@')
                if not username:
                    continue

                display_name = obj.name
                self.stdout.write(f'{label}: {display_name} → twitch.tv/{username}')

                # Resolve user
                user_id, tw_display, _ = resolve_user(username, client_id, token)
                if not user_id:
                    self.stdout.write(self.style.WARNING(f'  Could not resolve user: {username}'))
                    total_skipped += 1
                    continue

                channel_title = tw_display or display_name

                # ── Live status check ──────────────────────────
                live_info = check_live(username, client_id, token)
                live_key  = f'twitch_live_{username.lower()}'

                if live_info:
                    total_live += 1
                    self.stdout.write(self.style.SUCCESS(
                        f'  🔴 LIVE  "{live_info["title"][:50]}"  '
                        f'({live_info["viewer_count"]} viewers)'
                    ))
                    if not dry_run:
                        defaults = dict(
                            source_type        = VideoTrack.SOURCE_TWITCH_LIVE,
                            twitch_username    = username.lower(),
                            youtube_channel_id = '',
                            channel_title      = channel_title,
                            title              = live_info['title'] or f'{channel_title} — Live',
                            artist_name_display = display_name,
                            thumbnail_url      = live_info['thumbnail_url'],
                            is_live            = True,
                            live_checked_at    = timezone.now(),
                            live_viewer_count  = live_info['viewer_count'],
                            is_active          = True,
                        )
                        if isinstance(obj, Artist):          defaults['artist']   = obj
                        elif isinstance(obj, PromoterProfile): defaults['promoter'] = obj
                        elif isinstance(obj, Venue):          defaults['venue']    = obj

                        _, created = VideoTrack.objects.update_or_create(
                            youtube_video_id=live_key, defaults=defaults
                        )
                        if created: total_created += 1
                        else:       total_updated += 1
                else:
                    # Mark any existing live entry as offline
                    if not dry_run:
                        updated = VideoTrack.objects.filter(
                            youtube_video_id=live_key
                        ).update(is_live=False, live_checked_at=timezone.now())
                        if updated:
                            self.stdout.write(f'  📴 Was live, now offline')
                    else:
                        self.stdout.write(f'  📴 Offline')

                if live_only:
                    time.sleep(0.2)
                    continue

                # ── VOD harvest ────────────────────────────────
                try:
                    vods = get_vods(user_id, max_vods, client_id, token)
                except Exception as exc:
                    self.stdout.write(self.style.ERROR(f'  VOD fetch error: {exc}'))
                    total_skipped += 1
                    time.sleep(0.3)
                    continue

                self.stdout.write(f'  {len(vods)} VODs found')

                for vod in vods:
                    vod_id   = vod['id']          # numeric string e.g. '2345678901'
                    vod_key  = f'twitch_vod_{vod_id}'

                    pub_str = vod.get('published_at') or vod.get('created_at', '')
                    try:
                        pub_dt = datetime.fromisoformat(pub_str.replace('Z', '+00:00'))
                    except (ValueError, AttributeError):
                        pub_dt = None

                    thumb = vod.get('thumbnail_url', '').replace(
                        '%{width}', '640').replace('%{height}', '360')
                    # Twitch uses %{width} in VOD thumbnails
                    thumb = thumb.replace('%{width}', '640').replace('%{height}', '360')

                    duration_secs = parse_duration(vod.get('duration', ''))

                    if dry_run:
                        self.stdout.write(
                            f'  [DRY] {vod_id} — {vod["title"][:55]}  '
                            f'({vod.get("duration","?")})'
                        )
                        continue

                    defaults = dict(
                        source_type         = VideoTrack.SOURCE_TWITCH_VOD,
                        twitch_username     = username.lower(),
                        twitch_video_id     = vod_id,
                        youtube_channel_id  = '',
                        channel_title       = channel_title,
                        title               = vod['title'][:300] or f'{channel_title} stream',
                        artist_name_display = display_name,
                        description         = vod.get('description', '')[:2000],
                        thumbnail_url       = thumb,
                        published_at        = pub_dt,
                        duration_secs       = duration_secs,
                        is_live             = False,
                        is_active           = True,
                    )
                    if isinstance(obj, Artist):            defaults['artist']   = obj
                    elif isinstance(obj, PromoterProfile): defaults['promoter'] = obj
                    elif isinstance(obj, Venue):           defaults['venue']    = obj

                    _, created = VideoTrack.objects.update_or_create(
                        youtube_video_id=vod_key, defaults=defaults
                    )
                    if created: total_created += 1
                    else:       total_updated += 1

                self.stdout.write(self.style.SUCCESS(
                    f'  ✓ Done  (user_id: {user_id})'
                ))
                time.sleep(0.3)

        if source in ('artists', 'all'):
            process(Artist.objects.exclude(twitch='').order_by('name'), 'Artist')
        if source in ('promoters', 'all'):
            process(PromoterProfile.objects.exclude(twitch='').order_by('name'), 'Promoter')
        if source in ('venues', 'all'):
            process(Venue.objects.filter(active=True).exclude(twitch='').order_by('name'), 'Venue')

        prefix = '[DRY RUN] ' if dry_run else ''
        live_str = f', {total_live} currently live' if total_live else ''
        self.stdout.write(self.style.SUCCESS(
            f'\n{prefix}Done — {total_created} created, {total_updated} updated, '
            f'{total_skipped} skipped{live_str}'
        ))
