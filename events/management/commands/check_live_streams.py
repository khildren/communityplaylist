"""
check_live_streams — Fast live-status sweep for artists, promoters, and venues.

YouTube:  Zero-quota approach — GET /channel/{id}/live and check if YouTube
          redirects to a /watch?v= URL (only happens when a live stream is active).
          Channel IDs are resolved once from the youtube URL field (costs 1 API
          unit each, then cached in the model's youtube_channel_id field).

Twitch:   Helix /streams check using app access token (same pattern as harvest_twitch).

Sets is_live=True/False on Artist, PromoterProfile, and Venue.
Run every 10 minutes via cron.

Usage:
  python manage.py check_live_streams              # all sources
  python manage.py check_live_streams --dry-run    # print results, no DB writes
  python manage.py check_live_streams --youtube-only
  python manage.py check_live_streams --twitch-only
"""

import time
import logging

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from events.models import Artist, PromoterProfile, Venue
from events.management.commands.harvest_youtube_videos import extract_channel_id_from_url
from events.management.commands.harvest_twitch import get_access_token, check_live as twitch_check_live, TwitchUserNotFound

logger = logging.getLogger(__name__)

YT_LIVE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
}


def is_youtube_channel_live(channel_id):
    """
    Zero-quota YouTube live check.

    youtube.com/channel/{id}/live redirects to /watch?v=XXXX when there is an
    active live stream. When offline the final URL stays on the channel page.
    Returns True if live, False if offline or on any error.
    """
    url = f'https://www.youtube.com/channel/{channel_id}/live'
    try:
        r = requests.get(url, headers=YT_LIVE_HEADERS, timeout=10, allow_redirects=True)
        return '/watch?v=' in r.url
    except Exception as exc:
        logger.warning('YouTube live check failed for %s: %s', channel_id, exc)
        return False


def resolve_and_cache_channel_id(obj, api_key):
    """
    Return the YouTube channel ID for obj (Artist/PromoterProfile/Venue).
    Checks cached field first, then VideoTrack records, then resolves via API.
    Saves to model on first resolution.
    Returns empty string if unresolvable.
    """
    # Already cached on the model
    if obj.youtube_channel_id:
        return obj.youtube_channel_id

    # Try to pull from an existing VideoTrack (harvest_youtube already resolved it)
    track_qs = obj.videotracks.exclude(youtube_channel_id='') if hasattr(obj, 'videotracks') else None
    if track_qs:
        track = track_qs.first()
        if track and track.youtube_channel_id:
            obj.__class__.objects.filter(pk=obj.pk).update(youtube_channel_id=track.youtube_channel_id)
            return track.youtube_channel_id

    # Fall back to resolving from the youtube URL field (costs 0-1 API units)
    youtube_url = getattr(obj, 'youtube', '')
    if not youtube_url:
        return ''

    channel_id = extract_channel_id_from_url(youtube_url, api_key)
    if channel_id:
        obj.__class__.objects.filter(pk=obj.pk).update(youtube_channel_id=channel_id)
    return channel_id or ''


class Command(BaseCommand):
    help = 'Check live stream status for all connected YouTube and Twitch channels'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Print results without updating the database')
        parser.add_argument('--youtube-only', action='store_true',
                            help='Only check YouTube channels')
        parser.add_argument('--twitch-only', action='store_true',
                            help='Only check Twitch channels')

    def handle(self, *args, **options):
        dry_run      = options['dry_run']
        youtube_only = options['youtube_only']
        twitch_only  = options['twitch_only']

        api_key = getattr(settings, 'YOUTUBE_API_KEY', '')
        twitch_client_id     = getattr(settings, 'TWITCH_CLIENT_ID', '')
        twitch_client_secret = getattr(settings, 'TWITCH_CLIENT_SECRET', '')

        # Gather all querysets: artist, promoter, venue
        sources = [
            ('Artist',   Artist.objects.all()),
            ('Promoter', PromoterProfile.objects.filter(is_public=True)),
            ('Venue',    Venue.objects.filter(active=True)),
        ]

        # ── Twitch token ──────────────────────────────────────────────────────
        twitch_token = None
        if not youtube_only and twitch_client_id and twitch_client_secret:
            try:
                twitch_token = get_access_token(twitch_client_id, twitch_client_secret)
                self.stdout.write('Twitch token OK')
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f'Twitch token failed: {exc} — skipping Twitch'))
        elif not youtube_only and (not twitch_client_id or not twitch_client_secret):
            self.stdout.write(self.style.WARNING(
                'TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET not set — skipping Twitch'
            ))

        # ── Per-source sweep ──────────────────────────────────────────────────
        live_now  = []
        went_live = []
        went_off  = []

        for label, qs in sources:
            for obj in qs:
                was_live = obj.is_live
                now_live = False

                # YouTube check
                if not twitch_only:
                    has_youtube = bool(getattr(obj, 'youtube', ''))
                    if has_youtube:
                        channel_id = resolve_and_cache_channel_id(obj, api_key) if (obj.youtube_channel_id or api_key) else ''
                        if channel_id:
                            yt_live = is_youtube_channel_live(channel_id)
                            if yt_live:
                                now_live = True
                                self.stdout.write(self.style.SUCCESS(
                                    f'  🔴 YT LIVE  {label}: {obj.name}'
                                ))
                            time.sleep(0.2)  # be polite

                # Twitch check
                if not youtube_only and twitch_token:
                    twitch_user = getattr(obj, 'twitch', '').strip().lstrip('@')
                    if twitch_user:
                        try:
                            info = twitch_check_live(twitch_user, twitch_client_id, twitch_token)
                            if info:
                                now_live = True
                                self.stdout.write(self.style.SUCCESS(
                                    f'  🔴 TW LIVE  {label}: {obj.name}  '
                                    f'({info["viewer_count"]} viewers)'
                                ))
                            # Clear orphan flag if it was previously set
                            if not dry_run and getattr(obj, 'twitch_unresolvable', False):
                                obj.__class__.objects.filter(pk=obj.pk).update(twitch_unresolvable=False)
                        except TwitchUserNotFound:
                            self.stdout.write(f'  ⚠ Twitch orphan: {label} {obj.name!r} — "{twitch_user}" not found')
                            if not dry_run:
                                obj.__class__.objects.filter(pk=obj.pk).update(twitch_unresolvable=True)
                        except Exception as exc:
                            logger.warning('Twitch check failed for %s: %s', obj.name, exc)
                        time.sleep(0.1)

                # Update model if status changed (or just log in dry-run)
                if now_live:
                    live_now.append(f'{label}: {obj.name}')
                if now_live and not was_live:
                    went_live.append(obj.name)
                elif was_live and not now_live:
                    went_off.append(obj.name)

                if not dry_run and now_live != was_live:
                    obj.__class__.objects.filter(pk=obj.pk).update(is_live=now_live)

        # ── Summary ───────────────────────────────────────────────────────────
        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write('')
        if live_now:
            self.stdout.write(self.style.SUCCESS(
                f'{prefix}{len(live_now)} currently live: ' + ', '.join(live_now)
            ))
        else:
            self.stdout.write(f'{prefix}No one live right now.')
        if went_live:
            self.stdout.write(self.style.SUCCESS(f'  ↑ Went live: ' + ', '.join(went_live)))
        if went_off:
            self.stdout.write(f'  ↓ Went offline: ' + ', '.join(went_off))
