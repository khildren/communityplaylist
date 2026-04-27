"""
harvest_instagram — pull recent posts from tracked InstagramAccount records.

Uses instaloader with a saved session for reliable, rate-limit-aware fetching.
Without a session it still works but Instagram throttles anonymous requests hard.

First-time setup (run once, interactively):
    python manage.py setup_instagram_session

Then harvest:
    python manage.py harvest_instagram
    python manage.py harvest_instagram --handle rave.pdx
    python manage.py harvest_instagram --dry-run
    python manage.py harvest_instagram --force       # ignore the 24h cooldown
    python manage.py harvest_instagram --count 6     # max posts to keep per account
"""
import os
import time
import instaloader
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q

# Session dir defaults to MEDIA_ROOT/.ig_session — works on both Docker and Plesk.
# Override with IG_SESSION_DIR env var if needed.
def _session_dir():
    default = os.path.join(os.environ.get('MEDIA_ROOT', ''), '.ig_session')
    if not default.startswith('/'):
        from django.conf import settings
        default = os.path.join(str(settings.MEDIA_ROOT), '.ig_session')
    return os.environ.get('IG_SESSION_DIR', default)

SESSION_DIR  = _session_dir()
SESSION_FILE = os.path.join(SESSION_DIR, 'session')


def _get_loader():
    """Return an Instaloader instance, logged in if a session file exists."""
    L = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        quiet=True,
    )
    if os.path.exists(SESSION_FILE):
        try:
            L.load_session_from_file(username=None, filename=SESSION_FILE)
        except Exception:
            pass  # session stale — continue anonymously, warn below
    return L


def _fetch_profile_private_api(L, handle: str) -> dict | None:
    """
    Fetch profile metadata via Instagram's private web API.
    Avoids the graphql endpoint that gets throttled quickly.
    Returns dict with keys: userid, full_name, biography, followers, is_private
    or None if not found / blocked.
    """
    session = L.context._session
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15',
        'X-IG-App-ID': '936619743392459',
    })
    r = session.get(
        'https://i.instagram.com/api/v1/users/web_profile_info/',
        params={'username': handle},
        timeout=15,
    )
    if not r.ok:
        return None
    user = r.json().get('data', {}).get('user')
    if not user:
        return None
    return {
        'userid':    str(user.get('id', '')),
        'full_name': user.get('full_name', ''),
        'biography': user.get('biography', ''),
        'followers': user.get('edge_followed_by', {}).get('count') or 0,
        'is_private': user.get('is_private', False),
    }


def _fetch_posts_private_api(L, user_id: str, max_posts: int) -> list[dict]:
    """
    Fetch recent posts via Instagram's private mobile API.
    Instaloader's get_posts() uses a graphql endpoint that Instagram deprecated;
    this endpoint (used by the mobile app) still works with a valid session.
    Returns a list of dicts: {ig_post_id, shortcode, caption, image_url, is_video, posted_at}
    """
    import datetime
    session = L.context._session
    session.headers.update({
        'User-Agent': 'Instagram 275.0.0.27.98 Android',
        'X-IG-App-ID': '936619743392459',
    })
    results = []
    max_id  = None
    while len(results) < max_posts:
        params = {'count': min(12, max_posts - len(results))}
        if max_id:
            params['max_id'] = max_id
        r = session.get(
            f'https://i.instagram.com/api/v1/feed/user/{user_id}/',
            params=params,
            timeout=20,
        )
        if not r.ok:
            break
        data  = r.json()
        items = data.get('items', [])
        if not items:
            break
        for item in items:
            shortcode = item.get('code', '')
            ig_post_id = str(item.get('pk', '') or item.get('id', ''))
            is_video   = item.get('media_type') == 2
            ts         = item.get('taken_at')
            posted_at  = datetime.datetime.utcfromtimestamp(ts).replace(
                tzinfo=datetime.timezone.utc) if ts else None
            caption = ''
            cap_obj = item.get('caption')
            if isinstance(cap_obj, dict):
                caption = cap_obj.get('text', '')
            # Best image: first candidate of image_versions2 (highest res)
            image_url = ''
            candidates = item.get('image_versions2', {}).get('candidates', [])
            if candidates:
                image_url = candidates[0].get('url', '')
            # Carousel: use first child's image
            if not image_url and item.get('carousel_media'):
                child = item['carousel_media'][0]
                cands = child.get('image_versions2', {}).get('candidates', [])
                if cands:
                    image_url = cands[0].get('url', '')
            # Usertags — people tagged in this post
            tagged = [
                t['user']['username']
                for t in item.get('usertags', {}).get('in', [])
                if t.get('user', {}).get('username')
            ]
            # Location
            loc_raw = item.get('location') or {}
            location = {
                'name': loc_raw.get('name', ''),
                'ig_location_id': str(loc_raw.get('pk', '') or ''),
                'lat': loc_raw.get('lat'),
                'lng': loc_raw.get('lng'),
            } if loc_raw else None
            results.append({
                'ig_post_id':      ig_post_id,
                'shortcode':       shortcode,
                'caption':         caption,
                'image_url':       image_url,
                'is_video':        is_video,
                'posted_at':       posted_at,
                'tagged_handles':  tagged,
                'location':        location,
            })
        if not data.get('more_available'):
            break
        max_id = data.get('next_max_id')
    return results[:max_posts]


class Command(BaseCommand):
    help = 'Harvest recent posts from tracked Instagram accounts via instaloader.'

    def add_arguments(self, parser):
        parser.add_argument('--handle', type=str, default='',
                            help='Harvest a single account by handle')
        parser.add_argument('--force', action='store_true',
                            help='Re-fetch accounts regardless of last_fetched time')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print what would be saved without writing to DB')
        parser.add_argument('--count', type=int, default=12,
                            help='Max recent posts to fetch per account (default: 12)')

    def handle(self, *args, **options):
        from events.models import InstagramAccount, InstagramPost

        handle   = options['handle'].lstrip('@').strip()
        force    = options['force']
        dry_run  = options['dry_run']
        max_posts = options['count']

        qs = InstagramAccount.objects.filter(status=InstagramAccount.STATUS_ACTIVE)
        if handle:
            qs = qs.filter(handle__iexact=handle)
        if not force:
            qs = qs.filter(
                Q(last_fetched__isnull=True) |
                Q(last_fetched__lt=timezone.now() - timezone.timedelta(hours=24))
            )

        accounts = list(qs)
        if not accounts:
            self.stdout.write('No accounts due for harvest.')
            return

        L = _get_loader()
        has_session = os.path.exists(SESSION_FILE)
        if not has_session:
            self.stdout.write(
                self.style.WARNING(
                    'No session file found — running anonymously (rate limits will be tight).\n'
                    'Run: python manage.py setup_instagram_session'
                )
            )

        self.stdout.write(f'Harvesting {len(accounts)} account(s) | max {max_posts} posts each…')
        total_new = 0

        for i, account in enumerate(accounts):
            if i > 0:
                # instaloader has its own internal rate limiting; this is extra headroom
                pause = 8 if has_session else 20
                self.stdout.write(f'  waiting {pause}s…')
                time.sleep(pause)

            self.stdout.write(f'  @{account.handle}')

            profile = _fetch_profile_private_api(L, account.handle)
            if not profile:
                self.stdout.write('    — profile not found, private, or rate-limited')
                continue
            if profile['is_private']:
                self.stdout.write('    — private account, skipping')
                continue

            # Update account metadata
            if not dry_run:
                account.ig_user_id     = profile['userid']
                account.display_name   = profile['full_name'] or account.display_name
                account.bio            = profile['biography'] or account.bio
                account.follower_count = profile['followers']
                account.last_fetched   = timezone.now()
                account.save(update_fields=[
                    'ig_user_id', 'display_name', 'bio', 'follower_count', 'last_fetched'
                ])

            self.stdout.write(
                f"    {profile['full_name']} | {profile['followers']:,} followers"
            )

            new_count = 0
            try:
                posts = _fetch_posts_private_api(L, profile['userid'], max_posts)
            except Exception as e:
                self.stderr.write(f'    fetch error: {e}')
                continue

            all_tagged = set()
            for p in posts:
                if not p['posted_at']:
                    continue
                if dry_run:
                    tagged_str = ', '.join(f'@{h}' for h in p['tagged_handles']) if p['tagged_handles'] else ''
                    self.stdout.write(
                        f"    [dry] {p['shortcode']} | "
                        f"{p['posted_at'].strftime('%Y-%m-%d')} | "
                        f"{p['caption'][:60]}"
                        + (f' | tags: {tagged_str}' if tagged_str else '')
                    )
                    all_tagged.update(p['tagged_handles'])
                    continue
                post, created = InstagramPost.objects.get_or_create(
                    ig_post_id=p['ig_post_id'],
                    defaults={
                        'account':        account,
                        'shortcode':      p['shortcode'],
                        'caption':        p['caption'],
                        'image_url':      p['image_url'],
                        'is_video':       p['is_video'],
                        'posted_at':      p['posted_at'],
                        'tagged_handles': p['tagged_handles'],
                    }
                )
                if created:
                    new_count += 1
                elif p['tagged_handles'] and post.tagged_handles != p['tagged_handles']:
                    post.tagged_handles = p['tagged_handles']
                    post.save(update_fields=['tagged_handles'])
                all_tagged.update(p['tagged_handles'])

            # ── Usertag discovery ─────────────────────────────────────────────
            # For each handle tagged across this account's posts, check if we
            # already know about them. Queue unknown handles as pending accounts.
            new_accounts = _discover_tagged_accounts(all_tagged, self.stdout)
            if new_accounts and not dry_run:
                self.stdout.write(
                    self.style.SUCCESS(f'    + {new_accounts} new account(s) queued for review')
                )

            if not dry_run:
                self.stdout.write(
                    f'    ✓ {new_count} new / {len(posts)} checked'
                    + (f' | {len(all_tagged)} unique tags seen' if all_tagged else '')
                )
                total_new += new_count

        self.stdout.write(f'\nDone. {total_new} new posts total.')


def _discover_tagged_accounts(handles: set, stdout=None) -> int:
    """
    For a set of Instagram handles seen in usertags, create pending
    InstagramAccount records for any that aren't already tracked.
    Also auto-links to existing Artist/PromoterProfile by instagram handle.
    Returns count of newly queued accounts.
    """
    from events.models import InstagramAccount, Artist, PromoterProfile

    known = set(InstagramAccount.objects.filter(
        handle__in=handles
    ).values_list('handle', flat=True))

    new_count = 0
    for handle in handles - known:
        if not handle or len(handle) > 100:
            continue

        # Auto-link to existing profiles by instagram handle field
        artist   = Artist.objects.filter(instagram__iexact=handle, is_stub=False).first()
        promoter = PromoterProfile.objects.filter(instagram__iexact=handle).first()

        acc = InstagramAccount.objects.create(
            handle             = handle,
            status             = InstagramAccount.STATUS_PENDING,
            is_active          = False,
            notes              = 'Auto-discovered via usertag',
            artist             = artist,
            promoter_profile   = promoter,
        )
        if stdout:
            linked = f' → links to {artist or promoter}' if (artist or promoter) else ''
            stdout.write(f'      queued @{handle}{linked}')
        new_count += 1

    return new_count
