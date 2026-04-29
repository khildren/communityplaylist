"""
archive_ended_streams — After a Twitch live stream ends, resolve its archive.

Priority: YouTube upload > Twitch VOD (YouTube pays artist more via AdSense).

For each recently ended Twitch live stream:
  1. Check DB for a YouTube VideoTrack from the same artist published within
     the archive window (2h before stream end → 72h after).
  2. If found in DB → deactivate matching Twitch VOD tracks (YouTube wins).
  3. If not in DB + YOUTUBE_API_KEY set → query YouTube Data API for a recent
     upload from the artist's channel, create a VideoTrack if found, deactivate Twitch VODs.
  4. If no YouTube found → leave Twitch VODs active (already created by harvest_twitch).

Suggested cron: every 30 minutes, after check_live_streams runs.
"""

import logging
import time
from datetime import datetime, timedelta

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from events.models import VideoTrack
from events.management.commands.harvest_twitch import (
    get_access_token, resolve_user, get_vods, parse_duration,
)

logger = logging.getLogger(__name__)

YOUTUBE_SEARCH_URL = 'https://www.googleapis.com/youtube/v3/search'
YOUTUBE_VIDEOS_URL = 'https://www.googleapis.com/youtube/v3/videos'

# How many hours after stream end to accept a YouTube upload as "same session"
YT_WINDOW_HOURS = 72
# How many hours before stream-end to also check (stream may have already been uploading)
YT_WINDOW_PRE_HOURS = 2


def _entity_filter(qs, track):
    """Filter a queryset by whichever artist/promoter/venue the track belongs to."""
    if track.artist_id:
        return qs.filter(artist_id=track.artist_id)
    if track.promoter_id:
        return qs.filter(promoter_id=track.promoter_id)
    if track.venue_id:
        return qs.filter(venue_id=track.venue_id)
    return qs.none()


def _find_yt_in_db(track, stream_ended_at):
    """Return the best YouTube VideoTrack for this artist published near stream_ended_at."""
    window_start = stream_ended_at - timedelta(hours=YT_WINDOW_PRE_HOURS)
    window_end   = stream_ended_at + timedelta(hours=YT_WINDOW_HOURS)
    qs = VideoTrack.objects.filter(
        source_type=VideoTrack.SOURCE_YOUTUBE,
        is_active=True,
        published_at__gte=window_start,
        published_at__lte=window_end,
    )
    return _entity_filter(qs, track).order_by('-published_at').first()


def _fetch_yt_upload_via_api(channel_id, after_dt, api_key):
    """
    Search YouTube for uploads from channel_id published after after_dt.
    Returns (video_id, snippet) or (None, None).
    """
    try:
        r = requests.get(YOUTUBE_SEARCH_URL, params={
            'key':           api_key,
            'channelId':     channel_id,
            'part':          'id,snippet',
            'type':          'video',
            'order':         'date',
            'publishedAfter': after_dt.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'maxResults':    1,
        }, timeout=15)
        r.raise_for_status()
        items = r.json().get('items', [])
        if not items:
            return None, None
        item = items[0]
        return item['id']['videoId'], item['snippet']
    except Exception as exc:
        logger.warning('YouTube search API failed for channel %s: %s', channel_id, exc)
        return None, None


def _get_yt_duration(video_id, api_key):
    """Return duration_secs for a YouTube video ID (costs 1 API unit)."""
    try:
        r = requests.get(YOUTUBE_VIDEOS_URL, params={
            'key':  api_key,
            'id':   video_id,
            'part': 'contentDetails',
        }, timeout=10)
        r.raise_for_status()
        items = r.json().get('items', [])
        if not items:
            return None
        import re
        iso = items[0].get('contentDetails', {}).get('duration', '')
        m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', iso)
        if not m:
            return None
        h, mn, s = (int(x or 0) for x in m.groups())
        return h * 3600 + mn * 60 + s
    except Exception:
        return None


def _get_channel_id(track):
    """Return YouTube channel_id for the track's linked entity, or ''."""
    for obj in [track.artist, track.promoter, track.venue]:
        if obj and getattr(obj, 'youtube_channel_id', ''):
            return obj.youtube_channel_id
    return ''


def _create_yt_track(track, video_id, snippet, channel_id, api_key, stdout):
    """Create or update a YouTube VideoTrack for the just-found upload."""
    pub_str = snippet.get('publishedAt', '')
    try:
        pub_dt = datetime.fromisoformat(pub_str.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        pub_dt = None

    thumb = (
        snippet.get('thumbnails', {}).get('maxres', {}).get('url')
        or snippet.get('thumbnails', {}).get('high', {}).get('url', '')
    )
    duration_secs = _get_yt_duration(video_id, api_key) if api_key else None

    defaults = dict(
        source_type         = VideoTrack.SOURCE_YOUTUBE,
        youtube_channel_id  = channel_id,
        channel_title       = snippet.get('channelTitle', track.channel_title),
        title               = snippet.get('title', track.title)[:300],
        artist_name_display = track.artist_name_display,
        thumbnail_url       = thumb,
        published_at        = pub_dt,
        duration_secs       = duration_secs,
        is_live             = False,
        is_active           = True,
    )
    if track.artist_id:   defaults['artist']   = track.artist
    if track.promoter_id: defaults['promoter'] = track.promoter
    if track.venue_id:    defaults['venue']    = track.venue

    _, created = VideoTrack.objects.update_or_create(
        youtube_video_id=video_id, defaults=defaults
    )
    stdout.write(f'  {"Created" if created else "Updated"} YouTube VideoTrack: {video_id}')


def _deactivate_vods(track, stream_ended_at, stdout):
    """Deactivate Twitch VOD tracks for the same channel published near stream end."""
    window_start = stream_ended_at - timedelta(hours=6)
    window_end   = stream_ended_at + timedelta(hours=6)
    qs = VideoTrack.objects.filter(
        source_type=VideoTrack.SOURCE_TWITCH_VOD,
        twitch_username=track.twitch_username,
    )
    # Also match by time window if published_at is set; fall back to all if not
    qs_timed = qs.filter(published_at__gte=window_start, published_at__lte=window_end)
    to_deactivate = qs_timed if qs_timed.exists() else qs.filter(is_active=True)
    count = to_deactivate.update(is_active=False)
    if count:
        stdout.write(f'  ↓ Deactivated {count} Twitch VOD(s) — YouTube preferred')


class Command(BaseCommand):
    help = 'Resolve ended Twitch streams to YouTube uploads (preferred) or Twitch VODs'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Print plan without writing to DB')
        parser.add_argument('--lookback-hours', type=int, default=48,
                            help='Hours to look back for recently ended streams (default 48)')
        parser.add_argument('--force-vod', action='store_true',
                            help='Also fetch Twitch VODs even when YouTube is found')

    def handle(self, *args, **options):
        dry_run        = options['dry_run']
        lookback_hours = options['lookback_hours']
        force_vod      = options['force_vod']
        now            = timezone.now()
        lookback_start = now - timedelta(hours=lookback_hours)

        client_id     = getattr(settings, 'TWITCH_CLIENT_ID', '')
        client_secret = getattr(settings, 'TWITCH_CLIENT_SECRET', '')
        api_key       = getattr(settings, 'YOUTUBE_API_KEY', '')

        # Obtain Twitch token (needed to fetch VODs as fallback)
        twitch_token = None
        if client_id and client_secret:
            try:
                twitch_token = get_access_token(client_id, client_secret)
                self.stdout.write('Twitch token OK')
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f'Twitch token failed: {exc}'))

        # Find recently ended Twitch live streams
        ended = list(
            VideoTrack.objects.filter(
                source_type=VideoTrack.SOURCE_TWITCH_LIVE,
                is_live=False,
                live_checked_at__gte=lookback_start,
            ).select_related('artist', 'promoter', 'venue')
        )

        if not ended:
            self.stdout.write('No recently ended Twitch streams found.')
            return

        self.stdout.write(f'Found {len(ended)} recently ended stream(s)\n')

        yt_chosen = vod_chosen = skipped = 0

        for track in ended:
            username       = track.twitch_username
            stream_ended   = track.live_checked_at or now
            label          = track.artist_name_display or track.channel_title or username
            ended_str      = stream_ended.strftime('%Y-%m-%d %H:%M UTC')

            self.stdout.write(f'→ {label} (twitch.tv/{username}, ended ~{ended_str})')

            # ── 1. Check DB for matching YouTube upload ──────────────────────
            yt_track = _find_yt_in_db(track, stream_ended)
            if yt_track:
                self.stdout.write(self.style.SUCCESS(
                    f'  ✓ YouTube in DB: "{yt_track.title[:55]}" '
                    f'(pub {yt_track.published_at.strftime("%Y-%m-%d")})'
                ))
                if not dry_run:
                    _deactivate_vods(track, stream_ended, self.stdout)
                self.stdout.write('  → YouTube wins (higher artist revenue)')
                yt_chosen += 1
                time.sleep(0.1)
                continue

            # ── 2. Check YouTube API for upload not yet harvested ────────────
            channel_id = _get_channel_id(track)
            if channel_id and api_key:
                search_after = stream_ended - timedelta(hours=YT_WINDOW_PRE_HOURS)
                video_id, snippet = _fetch_yt_upload_via_api(channel_id, search_after, api_key)
                if video_id:
                    self.stdout.write(self.style.SUCCESS(
                        f'  ✓ YouTube via API: "{snippet.get("title","")[:55]}"'
                    ))
                    if not dry_run:
                        _create_yt_track(track, video_id, snippet, channel_id, api_key, self.stdout)
                        _deactivate_vods(track, stream_ended, self.stdout)
                    self.stdout.write('  → YouTube wins (higher artist revenue)')
                    yt_chosen += 1
                    time.sleep(0.5)
                    continue
                time.sleep(0.3)

            # ── 3. Fall back to Twitch VOD ───────────────────────────────────
            if not twitch_token:
                self.stdout.write('  ⏳ No Twitch token — cannot fetch VOD, will retry next run')
                skipped += 1
                continue

            user_id = None
            try:
                user_id, _, _ = resolve_user(username, client_id, twitch_token)
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f'  ✗ Twitch resolve failed: {exc}'))
            if not user_id:
                skipped += 1
                time.sleep(0.2)
                continue

            vod = None
            try:
                vods = get_vods(user_id, 3, client_id, twitch_token)
                # Pick the VOD whose published_at is closest to stream_ended
                for v in vods:
                    pub_str = v.get('published_at') or v.get('created_at', '')
                    try:
                        pub_dt = datetime.fromisoformat(pub_str.replace('Z', '+00:00'))
                        if pub_dt >= stream_ended - timedelta(hours=24):
                            vod = v
                            break
                    except (ValueError, AttributeError):
                        pass
                if not vod and vods:
                    vod = vods[0]   # best guess: most recent
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f'  ✗ VOD fetch error: {exc}'))

            if vod:
                vod_id  = vod['id']
                vod_key = f'twitch_vod_{vod_id}'
                self.stdout.write(
                    f'  ✓ Twitch VOD: "{vod.get("title","")[:55]}" ({vod.get("duration","?")})'
                )
                if not dry_run:
                    self._ensure_vod_active(track, vod, vod_id, vod_key)
                self.stdout.write('  → VOD active (YouTube upload pending or unavailable)')
                vod_chosen += 1
            else:
                self.stdout.write('  ⏳ No VOD available yet — stream may still be processing')
                skipped += 1

            time.sleep(0.3)

        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(
            f'\n{prefix}Done — {yt_chosen} YouTube, {vod_chosen} Twitch VOD, {skipped} pending'
        )

    def _ensure_vod_active(self, live_track, vod, vod_id, vod_key):
        """Create or activate a Twitch VOD VideoTrack (idempotent)."""
        pub_str = vod.get('published_at') or vod.get('created_at', '')
        try:
            pub_dt = datetime.fromisoformat(pub_str.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            pub_dt = None

        thumb = vod.get('thumbnail_url', '').replace('%{width}', '640').replace('%{height}', '360')
        duration_secs = parse_duration(vod.get('duration', ''))

        defaults = dict(
            source_type         = VideoTrack.SOURCE_TWITCH_VOD,
            twitch_username     = live_track.twitch_username,
            twitch_video_id     = vod_id,
            youtube_channel_id  = '',
            channel_title       = live_track.channel_title,
            title               = (vod.get('title') or f'{live_track.channel_title} stream')[:300],
            artist_name_display = live_track.artist_name_display,
            thumbnail_url       = thumb,
            published_at        = pub_dt,
            duration_secs       = duration_secs,
            is_live             = False,
            is_active           = True,
        )
        if live_track.artist_id:   defaults['artist']   = live_track.artist
        if live_track.promoter_id: defaults['promoter'] = live_track.promoter
        if live_track.venue_id:    defaults['venue']    = live_track.venue

        _, created = VideoTrack.objects.update_or_create(
            youtube_video_id=vod_key, defaults=defaults
        )
        self.stdout.write(f'  {"Created" if created else "Activated"} Twitch VOD track: {vod_id}')
