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

# Where the instaloader session file lives inside the container.
# Mount a persistent volume here so it survives image rebuilds.
SESSION_DIR  = os.environ.get('IG_SESSION_DIR', '/app/media/.ig_session')
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

            try:
                profile = instaloader.Profile.from_username(L.context, account.handle)
            except instaloader.exceptions.ProfileNotExistsException:
                self.stdout.write('    — profile not found or private')
                continue
            except instaloader.exceptions.TooManyRequestsException:
                self.stderr.write('    rate-limited — stopping early, try again later')
                break
            except Exception as e:
                self.stderr.write(f'    ERROR: {e}')
                continue

            # Update account metadata from profile
            if not dry_run:
                account.ig_user_id     = str(profile.userid)
                account.display_name   = profile.full_name or account.display_name
                account.bio            = profile.biography or account.bio
                account.follower_count = profile.followers
                account.last_fetched   = timezone.now()
                account.save(update_fields=[
                    'ig_user_id', 'display_name', 'bio', 'follower_count', 'last_fetched'
                ])

            self.stdout.write(
                f'    {profile.full_name} | {profile.followers:,} followers'
            )

            new_count = 0
            fetched   = 0
            try:
                for post in profile.get_posts():
                    if fetched >= max_posts:
                        break
                    fetched += 1

                    posted_at = timezone.make_aware(
                        post.date_local.replace(tzinfo=None),
                        timezone.utc
                    ) if post.date_utc else None

                    if not posted_at:
                        continue

                    if dry_run:
                        self.stdout.write(
                            f'    [dry] {post.shortcode} | '
                            f'{post.date_utc.strftime("%Y-%m-%d")} | '
                            f'{(post.caption or "")[:80]}'
                        )
                        continue

                    _, created = InstagramPost.objects.get_or_create(
                        ig_post_id=str(post.mediaid),
                        defaults={
                            'account':   account,
                            'shortcode': post.shortcode,
                            'caption':   post.caption or '',
                            'image_url': post.url or '',
                            'is_video':  post.is_video,
                            'posted_at': post.date_utc,
                        }
                    )
                    if created:
                        new_count += 1

            except instaloader.exceptions.TooManyRequestsException:
                self.stderr.write('    rate-limited mid-fetch — stopping early')
                break
            except Exception as e:
                self.stderr.write(f'    fetch error: {e}')
                continue

            if not dry_run:
                self.stdout.write(f'    ✓ {new_count} new / {fetched} checked')
                total_new += new_count

        self.stdout.write(f'\nDone. {total_new} new posts total.')
