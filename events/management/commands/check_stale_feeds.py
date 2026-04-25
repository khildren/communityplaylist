"""
check_stale_feeds — Flag active feeds that haven't synced recently.

Scans VenueFeed (active=True) and CalendarFeed entries for sources that:
  - Have never synced (last_synced is NULL), or
  - Haven't synced within the threshold (default 3 days for venue feeds,
    7 days for calendar feeds), or
  - Had an error on their last sync (last_error is non-empty).

Hard failures (403, 404, SSL) are highlighted separately. Use
--deactivate-hard-fails to automatically set active=False on those feeds.
Import runs will also auto-deactivate feeds with consecutive hard failures.

Usage:
  python manage.py check_stale_feeds
  python manage.py check_stale_feeds --venue-days 5 --cal-days 14
  python manage.py check_stale_feeds --deactivate-hard-fails
  python manage.py check_stale_feeds --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from events.models import CalendarFeed, VenueFeed
from events.utils.url_safety import is_hard_feed_failure as _is_hard_failure


class Command(BaseCommand):
    help = 'Report active feeds that are stale or erroring; flag/deactivate hard failures'

    def add_arguments(self, parser):
        parser.add_argument('--venue-days', type=int, default=3,
                            help='Days without sync before a VenueFeed is stale (default 3)')
        parser.add_argument('--cal-days', type=int, default=7,
                            help='Days without sync before a CalendarFeed is stale (default 7)')
        parser.add_argument('--deactivate-hard-fails', action='store_true',
                            help='Set active=False on feeds with hard failures (403/404/SSL)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print results without any DB writes')

    def handle(self, *args, **options):
        now          = timezone.now()
        venue_cutoff = now - timedelta(days=options['venue_days'])
        cal_cutoff   = now - timedelta(days=options['cal_days'])
        deactivate   = options['deactivate_hard_fails'] and not options['dry_run']
        dry_run      = options['dry_run']

        stale_count = soft_error_count = hard_error_count = never_count = deactivated_count = 0

        # ── VenueFeed ────────────────────────────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING('VenueFeed (active sources)'))
        venue_qs = VenueFeed.objects.filter(active=True).order_by('name')

        for vf in venue_qs:
            issues = []
            is_hard = False

            if vf.last_synced is None:
                issues.append('NEVER SYNCED')
                never_count += 1
            elif vf.last_synced < venue_cutoff:
                age = (now - vf.last_synced).days
                issues.append(f'STALE {age}d')
                stale_count += 1

            if vf.last_error:
                short_err = vf.last_error.strip().splitlines()[0][:100]
                is_hard = _is_hard_failure(vf.last_error)
                if is_hard:
                    issues.append(f'HARD FAIL: {short_err}')
                    hard_error_count += 1
                else:
                    issues.append(f'ERROR: {short_err}')
                    soft_error_count += 1

            if issues:
                style = self.style.ERROR if is_hard else self.style.WARNING
                self.stdout.write(style(f'  [{", ".join(issues)}]  {vf.name}'))
                if is_hard and deactivate:
                    VenueFeed.objects.filter(pk=vf.pk).update(active=False)
                    self.stdout.write(f'    ↳ deactivated')
                    deactivated_count += 1
            else:
                synced_ago = (now - vf.last_synced).total_seconds() / 3600
                self.stdout.write(f'  OK  {vf.name}  (synced {synced_ago:.1f}h ago)')

        # Also show recently auto-deactivated feeds (deactivated in last 7 days via import)
        recently_killed = VenueFeed.objects.filter(
            active=False,
            last_synced__gte=now - timedelta(days=7),
            last_error__gt='',
        ).order_by('name')
        if recently_killed.exists():
            self.stdout.write('')
            self.stdout.write(self.style.MIGRATE_HEADING('Recently auto-deactivated feeds'))
            for vf in recently_killed:
                short_err = vf.last_error.strip().splitlines()[0][:100]
                self.stdout.write(
                    self.style.ERROR(f'  [DEACTIVATED]  {vf.name}  — {short_err}')
                )

        # ── CalendarFeed ─────────────────────────────────────────────────────
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('CalendarFeed (user calendars)'))
        cal_qs = CalendarFeed.objects.select_related('user').order_by('user__username')

        for cf in cal_qs:
            issues = []

            if cf.last_synced is None:
                issues.append('NEVER SYNCED')
                never_count += 1
            elif cf.last_synced < cal_cutoff:
                age = (now - cf.last_synced).days
                issues.append(f'STALE {age}d')
                stale_count += 1

            label = cf.label or cf.url[:60]
            owner = cf.user.username if cf.user_id else '(no user)'

            if issues:
                self.stdout.write(
                    self.style.WARNING(f'  [{", ".join(issues)}]  {owner} / {label}')
                )
            else:
                synced_ago = (now - cf.last_synced).total_seconds() / 3600
                self.stdout.write(f'  OK  {owner} / {label}  (synced {synced_ago:.1f}h ago)')

        # ── Summary ───────────────────────────────────────────────────────────
        self.stdout.write('')
        total_issues = stale_count + soft_error_count + hard_error_count + never_count
        prefix = '[DRY RUN] ' if dry_run else ''
        summary = (
            f'{prefix}VenueFeed: {venue_qs.count()} active  |  '
            f'CalendarFeed: {cal_qs.count()} total  |  '
            f'Never: {never_count}  Stale: {stale_count}  '
            f'Soft errors: {soft_error_count}  Hard fails: {hard_error_count}'
        )
        if deactivated_count:
            summary += f'  |  Auto-deactivated: {deactivated_count}'

        if total_issues:
            self.stdout.write(self.style.WARNING(summary))
        else:
            self.stdout.write(self.style.SUCCESS('All feeds healthy.  ' + summary))
