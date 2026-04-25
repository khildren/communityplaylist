"""
past_date_auto_reject — Auto-reject pending events whose start date has passed.

Events submitted and left in Pending Review after their start time serve no
purpose on the calendar and inflate the pending count. This command moves them
to 'rejected' with a reason so the count stays accurate.

Usage:
  python manage.py past_date_auto_reject
  python manage.py past_date_auto_reject --dry-run
  python manage.py past_date_auto_reject --days 3   # grace period (default 1)
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from events.models import Event


class Command(BaseCommand):
    help = 'Auto-reject pending events whose start date has passed'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--days', type=int, default=1,
                            help='Grace period in days past start_date (default 1)')

    def handle(self, *args, **options):
        dry_run    = options['dry_run']
        grace_days = options['days']
        cutoff     = timezone.now() - timedelta(days=grace_days)

        qs = Event.objects.filter(status='pending', start_date__lt=cutoff)
        total = qs.count()

        if not total:
            self.stdout.write('No stale pending events found.')
            return

        for ev in qs:
            age = (timezone.now() - ev.start_date).days
            self.stdout.write(
                f'  REJECT  [{age}d past] {ev.title[:60]}  ({ev.start_date:%Y-%m-%d})'
                + ('  [submitted by: ' + ev.submitted_by + ']' if ev.submitted_by else '')
            )

        if not dry_run:
            updated = qs.update(status='rejected')
            self.stdout.write(self.style.SUCCESS(
                f'Auto-rejected {updated} stale pending event{"s" if updated != 1 else ""}.'
            ))
        else:
            self.stdout.write(f'[DRY RUN] Would reject {total} events.')
