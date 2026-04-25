"""
check_stale_feeds — Flag active feeds that haven't synced recently.

Scans VenueFeed (active=True) and CalendarFeed entries for sources that:
  - Have never synced (last_synced is NULL), or
  - Haven't synced within the threshold (default 3 days for venue feeds,
    7 days for calendar feeds), or
  - Had an error on their last sync (last_error is non-empty).

Usage:
  python manage.py check_stale_feeds
  python manage.py check_stale_feeds --venue-days 5 --cal-days 14
  python manage.py check_stale_feeds --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from events.models import CalendarFeed, VenueFeed


class Command(BaseCommand):
    help = 'Report active feeds that are stale or erroring'

    def add_arguments(self, parser):
        parser.add_argument('--venue-days', type=int, default=3,
                            help='Days without sync before a VenueFeed is stale (default 3)')
        parser.add_argument('--cal-days', type=int, default=7,
                            help='Days without sync before a CalendarFeed is stale (default 7)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print results without any DB writes (currently informational only)')

    def handle(self, *args, **options):
        now          = timezone.now()
        venue_cutoff = now - timedelta(days=options['venue_days'])
        cal_cutoff   = now - timedelta(days=options['cal_days'])

        stale_count = error_count = never_count = 0

        # --- VenueFeed ---
        self.stdout.write(self.style.MIGRATE_HEADING('VenueFeed (active sources)'))
        venue_qs = VenueFeed.objects.filter(active=True).order_by('name')

        for vf in venue_qs:
            issues = []

            if vf.last_synced is None:
                issues.append('NEVER SYNCED')
                never_count += 1
            elif vf.last_synced < venue_cutoff:
                age = (now - vf.last_synced).days
                issues.append(f'STALE {age}d')
                stale_count += 1

            if vf.last_error:
                short_err = vf.last_error.strip().splitlines()[0][:120]
                issues.append(f'ERROR: {short_err}')
                error_count += 1

            if issues:
                self.stdout.write(
                    self.style.WARNING(f'  [{", ".join(issues)}]  {vf.name}')
                )
            else:
                synced_ago = (now - vf.last_synced).total_seconds() / 3600
                self.stdout.write(f'  OK  {vf.name}  (synced {synced_ago:.1f}h ago)')

        # --- CalendarFeed ---
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

        # --- Summary ---
        self.stdout.write('')
        total_issues = stale_count + error_count + never_count
        summary = (
            f'VenueFeed: {venue_qs.count()} active  |  '
            f'CalendarFeed: {cal_qs.count()} total  |  '
            f'Issues: {never_count} never-synced, {stale_count} stale, {error_count} erroring'
        )
        if total_issues:
            self.stdout.write(self.style.WARNING(summary))
        else:
            self.stdout.write(self.style.SUCCESS('All feeds healthy.  ' + summary))
