"""
management command: daily_digest

Posts today's Portland events to Discord as rich embeds.
- Up to SOCIAL_DAILY_POST_LIMIT events: one embed per event
- Over the limit: groups by category, one summary embed per group linking to filtered page

Run daily via cron (example: 8 AM):
    0 8 * * * docker exec cp-communityplaylist-1 python manage.py daily_digest
"""
import time
import requests
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.timezone import localtime
from django.conf import settings
from events.models import Event
from board.social import (
    _discord_send, events_by_category,
    EVENT_CATS, CP_BASE, LOGO, _venue_tag, _title_tags,
)

WEBHOOK = getattr(settings, 'DISCORD_WEBHOOK_EVENTS', '') or \
    'https://discord.com/api/webhooks/1487258605102039051/aMDBINHJSRTE2DVRB7AIdEQpC-5pacJgEKwEn9_gf6nhJbCLlsXD41zADDIlP-5Md5CC'

CAT_LABELS = {
    'music': '🎵 Music Tonight',
    'arts':  '🎨 Arts & Comedy',
    'food':  '🍎 Food & Community',
    'bike':  '🚲 Bike Events',
    'fund':  '💛 Fundraisers',
    'hybrid':'✦ Hybrid Events',
    '':      '🌹 Events',
}


class Command(BaseCommand):
    help = "Post today's PDX events to Discord"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        limit   = getattr(settings, 'SOCIAL_DAILY_POST_LIMIT', 27)

        now         = timezone.now()
        today_start = now.replace(hour=0,  minute=0,  second=0,  microsecond=0)
        today_end   = now.replace(hour=23, minute=59, second=59, microsecond=0)
        events = Event.objects.prefetch_related('genres').filter(
            status='approved',
            start_date__gte=today_start,
            start_date__lte=today_end,
        ).order_by('start_date')

        date_str = localtime(now).strftime('%A, %B %d')

        if not events.exists():
            if not dry_run:
                _discord_send(WEBHOOK, {'content': f'📅 No events today — {CP_BASE}'})
            return

        total = events.count()

        # Header
        header = f'🌹 **Today\'s PDX Events — {date_str}** ({total} events)\n{CP_BASE}'
        if dry_run:
            self.stdout.write(f'[DRY] header: {header}')
        else:
            _discord_send(WEBHOOK, {'content': header})
            time.sleep(1)

        if total <= limit:
            # One embed per event
            for e in events:
                if dry_run:
                    self.stdout.write(f'  [DRY] {e.title}')
                else:
                    _discord_send(WEBHOOK, {'embeds': [_event_embed(e)]})
                    time.sleep(1)
        else:
            # Split by category — one summary embed per group
            buckets = events_by_category(list(events))
            for cat, cat_events in buckets.items():
                if not cat_events:
                    continue
                label    = CAT_LABELS.get(cat, '🌹 Events')
                cat_path, cat_tag = EVENT_CATS.get(cat, ('/', '#PDXEvents'))
                link     = f'{CP_BASE}{cat_path}'

                lines = []
                for e in cat_events[:12]:
                    start = localtime(e.start_date).strftime('%-I:%M %p')
                    cost  = 'FREE' if e.is_free else (e.price_info or 'Paid')
                    eurl  = f'{CP_BASE}/events/{e.slug}/'
                    lines.append(f'• [{e.title}]({eurl}) — {start}, {cost}')
                if len(cat_events) > 12:
                    lines.append(f'… and {len(cat_events) - 12} more → [see all]({link})')

                embed = {
                    'title':       label,
                    'url':         link,
                    'description': '\n'.join(lines),
                    'color':       0xff6b35,
                    'footer':      {'text': f'{cat_tag} #Portland #PDX · communityplaylist.com',
                                    'icon_url': LOGO},
                }
                if dry_run:
                    self.stdout.write(f'  [DRY] {label}: {len(cat_events)} events')
                else:
                    _discord_send(WEBHOOK, {'embeds': [embed]})
                    time.sleep(1)

        self.stdout.write(f'[daily_digest] done — {total} events')


def _event_embed(event):
    url    = f'{CP_BASE}/events/{event.slug}/'
    genres = ', '.join(event.genres.values_list('name', flat=True)[:4]) or 'various'
    start  = localtime(event.start_date).strftime('%a %b %-d @ %-I:%M %p')
    cost   = 'FREE' if event.is_free else (event.price_info or 'Paid')
    img    = f'{CP_BASE}{event.photo.url}' if event.photo else LOGO
    vtag   = _venue_tag(event.location)
    ttag   = _title_tags(event.title)
    _, cat_tag = EVENT_CATS.get(event.category or '', EVENT_CATS[''])

    return {
        'title':       event.title,
        'url':         url,
        'description': (event.description or '')[:300],
        'color':       0xff6b35,
        'thumbnail':   {'url': img},
        'fields': [
            {'name': '📅 When',  'value': start,               'inline': True},
            {'name': '📍 Where', 'value': event.location[:80], 'inline': True},
            {'name': '🎵 Genre', 'value': genres,              'inline': True},
            {'name': '💰 Cost',  'value': cost,                'inline': True},
        ],
        'footer': {
            'text':     f'{ttag} {vtag} {cat_tag} #PDX · communityplaylist.com',
            'icon_url': LOGO,
        },
        'timestamp': event.start_date.strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
