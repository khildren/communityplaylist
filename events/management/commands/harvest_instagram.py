"""
harvest_instagram — pull recent posts from tracked InstagramAccount records.

Uses Instagram's internal profile API (same approach as enrich_instagram).
The web_profile_info endpoint returns the ~12 most recent posts in
edge_owner_to_timeline_media — no auth required, rate-limited to ~1/6s.

Run:
    python manage.py harvest_instagram
    python manage.py harvest_instagram --handle rave.pdx
    python manage.py harvest_instagram --dry-run
    python manage.py harvest_instagram --force   # re-fetch even recently-updated accounts
"""
import time
import json
import urllib.request
import urllib.parse
from django.core.management.base import BaseCommand
from django.utils import timezone

IG_API  = 'https://i.instagram.com/api/v1/users/web_profile_info/'
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
RATE_SLEEP = 6


def _fetch_profile(handle):
    params = urllib.parse.urlencode({'username': handle.lstrip('@')})
    req    = urllib.request.Request(f'{IG_API}?{params}', headers=IG_HDRS)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RuntimeError('Instagram rate-limit (429) — wait and retry')
        if e.code == 404:
            return None
        raise
    except Exception as e:
        raise RuntimeError(str(e))
    return data.get('data', {}).get('user') or None


def _parse_posts(user):
    """Extract post dicts from the edge_owner_to_timeline_media node."""
    edges = (
        (user.get('edge_owner_to_timeline_media') or {})
        .get('edges') or []
    )
    posts = []
    for edge in edges:
        node = edge.get('node', {})
        ig_id     = node.get('id', '')
        shortcode = node.get('shortcode', '')
        if not ig_id or not shortcode:
            continue
        caption_edges = (node.get('edge_media_to_caption') or {}).get('edges') or []
        caption = caption_edges[0]['node']['text'] if caption_edges else ''
        image_url = node.get('display_url') or node.get('thumbnail_src') or ''
        is_video  = bool(node.get('is_video'))
        timestamp = node.get('taken_at_timestamp')
        if not timestamp:
            continue
        posts.append({
            'ig_post_id': ig_id,
            'shortcode':  shortcode,
            'caption':    caption,
            'image_url':  image_url,
            'is_video':   is_video,
            'posted_at':  timezone.datetime.utcfromtimestamp(timestamp).replace(
                tzinfo=timezone.utc
            ),
        })
    return posts


class Command(BaseCommand):
    help = 'Harvest recent posts from tracked Instagram accounts.'

    def add_arguments(self, parser):
        parser.add_argument('--handle', type=str, default='',
                            help='Harvest a single account by handle')
        parser.add_argument('--force', action='store_true',
                            help='Re-fetch accounts updated within the last hour')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print what would be saved without writing to DB')

    def handle(self, *args, **options):
        from events.models import InstagramAccount, InstagramPost

        handle  = options['handle'].lstrip('@').strip()
        force   = options['force']
        dry_run = options['dry_run']

        from django.db.models import Q
        qs = InstagramAccount.objects.filter(status=InstagramAccount.STATUS_ACTIVE)
        if handle:
            qs = qs.filter(handle__iexact=handle)
        if not force:
            qs = qs.filter(
                Q(last_fetched__isnull=True) |
                Q(last_fetched__lt=timezone.now() - timezone.timedelta(hours=1))
            )

        accounts = list(qs)
        self.stdout.write(f'Harvesting {len(accounts)} account(s)…')

        total_new = 0

        for i, account in enumerate(accounts):
            if i > 0:
                time.sleep(RATE_SLEEP)

            self.stdout.write(f'  @{account.handle}')

            try:
                user = _fetch_profile(account.handle)
            except RuntimeError as e:
                self.stderr.write(f'    ERROR: {e}')
                time.sleep(15)
                continue
            except Exception as e:
                self.stderr.write(f'    ERROR: {e}')
                continue

            if not user:
                self.stdout.write(f'    — not found or private')
                continue

            # Update account metadata
            if not dry_run:
                account.ig_user_id     = user.get('id') or account.ig_user_id
                account.display_name   = user.get('full_name') or account.display_name
                account.bio            = (user.get('biography') or '').strip() or account.bio
                account.follower_count = (user.get('edge_followed_by') or {}).get('count')
                account.last_fetched   = timezone.now()
                account.save(update_fields=[
                    'ig_user_id', 'display_name', 'bio', 'follower_count', 'last_fetched'
                ])

            posts = _parse_posts(user)
            self.stdout.write(f'    {len(posts)} posts in response')

            new_count = 0
            for p in posts:
                if dry_run:
                    self.stdout.write(
                        f'    [dry] {p["shortcode"]} | '
                        f'{p["posted_at"].strftime("%Y-%m-%d")} | '
                        f'{p["caption"][:80]}'
                    )
                    continue
                _, created = InstagramPost.objects.get_or_create(
                    ig_post_id=p['ig_post_id'],
                    defaults={
                        'account':   account,
                        'shortcode': p['shortcode'],
                        'caption':   p['caption'],
                        'image_url': p['image_url'],
                        'is_video':  p['is_video'],
                        'posted_at': p['posted_at'],
                    }
                )
                if created:
                    new_count += 1

            if not dry_run:
                self.stdout.write(f'    ✓ {new_count} new posts saved')
                total_new += new_count

        self.stdout.write(f'\nDone. {total_new} new posts total.')
