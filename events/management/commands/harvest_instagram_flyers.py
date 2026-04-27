"""
harvest_instagram_flyers — scan recent Instagram posts with Ollama moondream
and auto-create pending Events when a flyer is detected.

Only processes accounts with harvest_for_events=True. Skips video posts and
posts older than --max-age days. Never duplicates: skips posts already scanned,
and checks for existing events with the same source shortcode.

Usage:
    python manage.py harvest_instagram_flyers
    python manage.py harvest_instagram_flyers --handle pdxrave.scene
    python manage.py harvest_instagram_flyers --max-age 14
    python manage.py harvest_instagram_flyers --dry-run
    python manage.py harvest_instagram_flyers --rescan   # re-run moondream on already-scanned posts
"""
import re
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify

from events.models import InstagramAccount, InstagramPost, Event, Genre
from events.utils.flyer_scan import scan_flyer

# Minimum fields moondream must return for us to bother creating an Event
_REQUIRED_FIELDS = {'title', 'date'}

# Caption keywords that strongly suggest an event flyer (fast pre-filter)
_EVENT_KEYWORDS = re.compile(
    r'\b(presents|ft\.?|feat\.?|doors|tickets|rsvp|event|lineup|dj set|live set|'
    r'\$\d|\d\+|21\+|18\+|free entry|ticket link|eventbrite|ra\.co|'
    r'monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b',
    re.I,
)


def _caption_looks_like_event(caption: str) -> bool:
    """Lightweight pre-filter before spending moondream time on the image."""
    if not caption:
        return True  # no caption — let moondream decide
    return bool(_EVENT_KEYWORDS.search(caption))


def _build_event(result: dict, post: 'InstagramPost') -> 'Event | None':
    """
    Construct an unsaved Event from moondream result + post metadata.
    Returns None if required fields are missing.
    """
    title = (result.get('title') or '').strip()[:200]
    date_str = result.get('date', '')
    if not title or not date_str:
        return None

    # Parse date + optional time
    time_str = result.get('start_time') or result.get('doors_time') or '20:00'
    try:
        from datetime import datetime
        dt = datetime.strptime(f'{date_str} {time_str}', '%Y-%m-%d %H:%M')
        start_date = timezone.make_aware(dt)
    except ValueError:
        return None

    location = ''
    if result.get('venue_name'):
        location = result['venue_name']
        if result.get('venue_address'):
            location = f"{location}, {result['venue_address']}"
    location = location[:300] or 'TBA'

    description = result.get('description') or result.get('extra_text') or ''
    if post.caption and len(post.caption) > len(description):
        description = post.caption  # caption often has more context than flyer text alone

    price_info = result.get('price', '')[:100]
    is_free    = 'free' in price_info.lower() if price_info else True

    ticket_url = result.get('ticket_url', '')
    website    = ticket_url[:500] if ticket_url else ''

    return Event(
        title       = title,
        description = description,
        location    = location,
        start_date  = start_date,
        price_info  = price_info,
        is_free     = is_free,
        website     = website,
        flyer_url   = post.permalink,
        flyer_scanned = True,
        submitted_by  = f'@{post.account.handle} (Instagram)',
        status        = 'pending',
    )


class Command(BaseCommand):
    help = 'Scan Instagram posts via Ollama moondream and create pending Events from detected flyers'

    def add_arguments(self, parser):
        parser.add_argument('--handle',   help='Only process this account handle')
        parser.add_argument('--max-age',  type=int, default=21,
                            help='Ignore posts older than N days (default 21)')
        parser.add_argument('--limit',    type=int, default=100,
                            help='Max posts to scan per run')
        parser.add_argument('--dry-run',  action='store_true')
        parser.add_argument('--rescan',   action='store_true',
                            help='Re-run moondream on already-scanned posts')

    def handle(self, *args, **options):
        dry    = options['dry_run']
        rescan = options['rescan']
        cutoff = timezone.now() - timedelta(days=options['max_age'])

        accounts = InstagramAccount.objects.filter(
            harvest_for_events=True,
            is_active=True,
            status=InstagramAccount.STATUS_ACTIVE,
        )
        if options['handle']:
            accounts = accounts.filter(handle__iexact=options['handle'])

        if not accounts.exists():
            self.stdout.write(self.style.WARNING(
                'No active accounts with harvest_for_events=True. '
                'Enable it on at least one InstagramAccount in admin.'
            ))
            return

        self.stdout.write(f'Accounts: {[a.handle for a in accounts]}\n')

        posts = InstagramPost.objects.filter(
            account__in=accounts,
            is_video=False,
            posted_at__gte=cutoff,
        )
        if not rescan:
            posts = posts.filter(flyer_scanned=False)
        posts = posts.order_by('-posted_at')[:options['limit']]

        total = posts.count()
        self.stdout.write(f'Posts to scan: {total} (dry={dry}, rescan={rescan})\n')

        created = skipped = failed = no_event = 0

        for post in posts:
            label = f'[@{post.account.handle} {post.shortcode}]'

            # Caption pre-filter — avoid wasting moondream on selfies/promos
            if not _caption_looks_like_event(post.caption):
                self.stdout.write(f'  {label} caption pre-filter skip')
                if not dry:
                    post.flyer_scanned = True
                    post.flyer_result  = {'_skip': 'caption pre-filter'}
                    post.save(update_fields=['flyer_scanned', 'flyer_result'])
                skipped += 1
                continue

            # Use image_url directly if available (already stored), else fall back to permalink
            scan_target = post.image_url or post.permalink
            self.stdout.write(f'  {label} scanning {scan_target[:70]}…')

            result = scan_flyer(scan_target)
            if not result:
                self.stdout.write(self.style.WARNING(f'    → moondream returned empty'))
                if not dry:
                    post.flyer_scanned = True
                    post.flyer_result  = {}
                    post.save(update_fields=['flyer_scanned', 'flyer_result'])
                failed += 1
                continue

            self.stdout.write(f'    → {list(result.keys())}')

            # Check for required fields
            if not (_REQUIRED_FIELDS <= set(k for k, v in result.items() if v)):
                self.stdout.write(f'    → missing required fields (title+date) — not an event')
                if not dry:
                    post.flyer_scanned = True
                    post.flyer_result  = result
                    post.save(update_fields=['flyer_scanned', 'flyer_result'])
                no_event += 1
                continue

            # Skip if we already sourced an event from this post
            if not rescan and post.sourced_event_id:
                self.stdout.write(f'    → already sourced event #{post.sourced_event_id}')
                skipped += 1
                continue

            event = _build_event(result, post)
            if not event:
                self.stdout.write(f'    → could not build event (bad date?)')
                if not dry:
                    post.flyer_scanned = True
                    post.flyer_result  = result
                    post.save(update_fields=['flyer_scanned', 'flyer_result'])
                no_event += 1
                continue

            self.stdout.write(self.style.SUCCESS(
                f'    ✓ "{event.title}" @ {event.location} on {event.start_date.date()}'
            ))

            if not dry:
                event.save()

                # Link artists by name if they exist in DB
                if result.get('artists'):
                    from events.models import Artist
                    for name in result['artists'][:8]:
                        a = Artist.objects.filter(name__iexact=name.strip(), is_stub=False).first()
                        if a:
                            event.artists.add(a)

                # Link genre if detected
                if result.get('genre'):
                    g = Genre.objects.filter(name__iexact=result['genre'].strip()).first()
                    if g:
                        event.genres.add(g)

                post.flyer_scanned  = True
                post.flyer_result   = result
                post.sourced_event  = event
                post.save(update_fields=['flyer_scanned', 'flyer_result', 'sourced_event'])

            created += 1

        self.stdout.write(self.style.SUCCESS(
            f'\nDone — created={created} no_event={no_event} skipped={skipped} failed={failed}'
        ))
        if created and not dry:
            self.stdout.write(
                f'Review new events at /admin/events/event/?status=pending'
            )
