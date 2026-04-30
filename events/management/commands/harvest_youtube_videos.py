"""
harvest_youtube_videos — Pull videos from YouTube channels connected to
artists, venues, and promoters. Stores them as VideoTrack records.

YouTube Data API v3 quota cost:
  - channels.list (to resolve handle → channel ID): 1 unit
  - playlistItems.list (50 videos per page): 1 unit per page
  - videos.list (duration lookup, 50 IDs per call): 1 unit per call

Total per source: ~3 units. At 10,000 units/day free tier, this supports
~3,000 sources per day before hitting limits. Well within our scale.

Usage:
  python manage.py harvest_youtube_videos           # all sources
  python manage.py harvest_youtube_videos --max 20  # limit to 20 videos per channel
  python manage.py harvest_youtube_videos --dry-run # print plan, no DB writes
"""

import re
import time
import logging
from datetime import datetime, timezone as dt_tz

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from events.models import Artist, PromoterProfile, Venue, VideoTrack

logger = logging.getLogger(__name__)

YT_API = 'https://www.googleapis.com/youtube/v3'


def _get(endpoint, params, api_key):
    """Single YouTube API GET with rate-limit backoff."""
    params['key'] = api_key
    for attempt in range(3):
        r = requests.get(f'{YT_API}/{endpoint}', params=params, timeout=15)
        if r.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def extract_channel_id_from_url(url, api_key):
    """
    Resolve any YouTube channel/user/handle URL to a raw channel ID (UCxxx…).
    Returns None if resolution fails or URL is not a YouTube channel URL.
    """
    if not url:
        return None
    url = url.strip().rstrip('/')

    # Direct /channel/UCxxx — no API call needed
    m = re.search(r'youtube\.com/channel/(UC[\w-]{22})', url)
    if m:
        return m.group(1)

    # @handle format — e.g. https://www.youtube.com/@artistname
    m = re.search(r'youtube\.com/@([\w.-]+)', url)
    if m:
        handle = m.group(1)
        try:
            data = _get('channels', {'part': 'id', 'forHandle': f'@{handle}'}, api_key)
            items = data.get('items', [])
            return items[0]['id'] if items else None
        except Exception as exc:
            logger.warning('Could not resolve @%s: %s', handle, exc)
            return None

    # Legacy /user/username
    m = re.search(r'youtube\.com/user/([\w.-]+)', url)
    if m:
        username = m.group(1)
        try:
            data = _get('channels', {'part': 'id', 'forUsername': username}, api_key)
            items = data.get('items', [])
            return items[0]['id'] if items else None
        except Exception as exc:
            logger.warning('Could not resolve user/%s: %s', username, exc)
            return None

    # /c/customname or bare custom name — try search channel
    m = re.search(r'youtube\.com/c/([\w.-]+)', url)
    if m:
        custom = m.group(1)
        try:
            data = _get('channels', {'part': 'id', 'forUsername': custom}, api_key)
            items = data.get('items', [])
            if items:
                return items[0]['id']
        except Exception:
            pass
        # Fall through to handle-style attempt
        try:
            data = _get('channels', {'part': 'id', 'forHandle': custom}, api_key)
            items = data.get('items', [])
            return items[0]['id'] if items else None
        except Exception as exc:
            logger.warning('Could not resolve /c/%s: %s', custom, exc)
            return None

    return None


def get_channel_info(channel_id, api_key):
    """Return (channel_title, uploads_playlist_id) for a channel."""
    data = _get('channels', {
        'part': 'snippet,contentDetails',
        'id': channel_id,
    }, api_key)
    items = data.get('items', [])
    if not items:
        return None, None
    item = items[0]
    title = item['snippet']['title']
    uploads_pl = item['contentDetails']['relatedPlaylists']['uploads']
    return title, uploads_pl


def get_video_durations(video_ids, api_key):
    """
    Batch-fetch duration (seconds) and embeddable flag for up to 50 video IDs.
    Returns {video_id: {'duration': int|None, 'embeddable': bool}}.
    """
    if not video_ids:
        return {}
    data = _get('videos', {
        'part': 'contentDetails,status',
        'id': ','.join(video_ids),
        'maxResults': 50,
    }, api_key)

    def iso_to_secs(iso):
        m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', iso)
        if not m:
            return None
        h, mn, s = (int(x or 0) for x in m.groups())
        return h * 3600 + mn * 60 + s

    return {
        item['id']: {
            'duration':   iso_to_secs(item.get('contentDetails', {}).get('duration', '')),
            'embeddable': item.get('status', {}).get('embeddable', True),
        }
        for item in data.get('items', [])
    }


def harvest_channel(channel_id, channel_title, source_obj, max_videos, api_key, dry_run=False):
    """
    Fetch up to max_videos from a channel's uploads playlist.
    source_obj is the Artist / PromoterProfile / Venue instance.
    Returns (created_count, updated_count).
    """
    _, uploads_pl = get_channel_info(channel_id, api_key)
    if not uploads_pl:
        logger.warning('No uploads playlist for channel %s', channel_id)
        return 0, 0

    created = updated = 0
    page_token = None
    fetched = 0

    while fetched < max_videos:
        params = {
            'part': 'snippet',
            'playlistId': uploads_pl,
            'maxResults': min(50, max_videos - fetched),
        }
        if page_token:
            params['pageToken'] = page_token

        data = _get('playlistItems', params, api_key)
        items = data.get('items', [])
        if not items:
            break

        # Collect IDs for duration batch fetch
        video_ids = [
            i['snippet']['resourceId']['videoId']
            for i in items
            if i['snippet'].get('resourceId', {}).get('videoId')
        ]
        durations = get_video_durations(video_ids, api_key) if not dry_run else {}

        for item in items:
            snippet = item['snippet']
            vid_id = snippet.get('resourceId', {}).get('videoId')
            if not vid_id:
                continue

            # Skip private/deleted videos (title == 'Private video' / 'Deleted video')
            if snippet.get('title') in ('Private video', 'Deleted video'):
                continue

            # Parse published_at
            pub_str = snippet.get('publishedAt', '')
            try:
                pub_dt = datetime.fromisoformat(pub_str.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                pub_dt = None

            # Best available thumbnail
            thumbs = snippet.get('thumbnails', {})
            thumb_url = (
                thumbs.get('maxres', {}).get('url') or
                thumbs.get('high', {}).get('url') or
                thumbs.get('medium', {}).get('url') or
                thumbs.get('default', {}).get('url') or ''
            )

            # Display name: linked object name > channel title
            if isinstance(source_obj, Artist):
                display_name = source_obj.name
            elif isinstance(source_obj, PromoterProfile):
                display_name = source_obj.name
            elif isinstance(source_obj, Venue):
                display_name = source_obj.name
            else:
                display_name = channel_title

            if dry_run:
                print(f'  [DRY] {vid_id} — {snippet["title"][:60]}')
                fetched += 1
                continue

            defaults = dict(
                youtube_channel_id = channel_id,
                channel_title      = channel_title,
                title              = snippet['title'][:300],
                description        = snippet.get('description', '')[:2000],
                thumbnail_url      = thumb_url,
                published_at       = pub_dt,
                duration_secs      = (durations.get(vid_id) or {}).get('duration'),
                yt_embeddable      = (durations.get(vid_id) or {}).get('embeddable', True),
                artist_name_display = display_name,
                is_active          = True,
            )
            # Set FK source
            if isinstance(source_obj, Artist):
                defaults['artist'] = source_obj
            elif isinstance(source_obj, PromoterProfile):
                defaults['promoter'] = source_obj
            elif isinstance(source_obj, Venue):
                defaults['venue'] = source_obj

            obj, was_created = VideoTrack.objects.update_or_create(
                youtube_video_id=vid_id,
                defaults=defaults,
            )
            if was_created:
                created += 1
            else:
                updated += 1
            fetched += 1

        page_token = data.get('nextPageToken')
        if not page_token:
            break

    return created, updated


class Command(BaseCommand):
    help = 'Harvest YouTube videos from connected artist/venue/promoter channels'

    def add_arguments(self, parser):
        parser.add_argument('--max', type=int, default=25,
                            help='Max videos to fetch per channel (default 25)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print plan without writing to DB')
        parser.add_argument('--source', choices=['artists', 'venues', 'promoters', 'all'],
                            default='all', help='Which source type to harvest')

    def handle(self, *args, **options):
        api_key = getattr(settings, 'YOUTUBE_API_KEY', '')
        if not api_key:
            raise CommandError(
                'YOUTUBE_API_KEY is not set in settings / .env\n'
                'Get one at: console.cloud.google.com → APIs & Services → '
                'Credentials → Create API Key → Enable YouTube Data API v3'
            )

        max_vids = options['max']
        dry_run  = options['dry_run']
        source   = options['source']

        total_created = total_updated = total_skipped = 0

        def process(qs, label):
            nonlocal total_created, total_updated, total_skipped
            for obj in qs:
                url = getattr(obj, 'youtube', '')
                if not url:
                    continue
                self.stdout.write(f'{label}: {obj.name} → {url}')
                channel_id = extract_channel_id_from_url(url, api_key)
                if not channel_id:
                    self.stdout.write(self.style.WARNING(f'  Could not resolve channel ID'))
                    total_skipped += 1
                    continue
                try:
                    channel_title, _ = get_channel_info(channel_id, api_key)
                    c, u = harvest_channel(
                        channel_id, channel_title or obj.name,
                        obj, max_vids, api_key, dry_run=dry_run,
                    )
                    total_created += c
                    total_updated += u
                    self.stdout.write(self.style.SUCCESS(
                        f'  ✓ {c} new, {u} updated  (ch: {channel_id})'
                    ))
                except Exception as exc:
                    self.stdout.write(self.style.ERROR(f'  ✗ Error: {exc}'))
                    total_skipped += 1
                # Be polite to the API
                time.sleep(0.3)

        if source in ('artists', 'all'):
            process(Artist.objects.exclude(youtube='').order_by('name'), 'Artist')
        if source in ('promoters', 'all'):
            process(PromoterProfile.objects.exclude(youtube='').order_by('name'), 'Promoter')
        if source in ('venues', 'all'):
            process(Venue.objects.filter(active=True).exclude(youtube='').order_by('name'), 'Venue')

        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(
            f'\n{prefix}Done — {total_created} created, {total_updated} updated, {total_skipped} skipped'
        ))
