"""
management command: link_recurring_events

Batch-links existing Event records to matching RecurringEvent records by title.
Safe to re-run — only updates events that have no recurring_event set.

Usage:
    python manage.py link_recurring_events
    python manage.py link_recurring_events --dry-run
"""
import re
import unicodedata
from django.core.management.base import BaseCommand
from events.models import Event, RecurringEvent


def _norm(title):
    t = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9 ]', '', t.lower()).strip()


class Command(BaseCommand):
    help = 'Link unlinked Events to RecurringEvents by title match'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        rmap = {_norm(r.title): r for r in RecurringEvent.objects.all()}
        self.stdout.write(f'Loaded {len(rmap)} recurring event titles')

        unlinked = Event.objects.filter(recurring_event__isnull=True)
        total = unlinked.count()
        self.stdout.write(f'Checking {total} unlinked events...')

        linked = skipped = 0
        for event in unlinked.iterator():
            key = _norm(event.title)
            match = rmap.get(key)
            if match:
                if dry_run:
                    self.stdout.write(f'  [DRY] "{event.title}" → "{match.title}"')
                else:
                    Event.objects.filter(pk=event.pk).update(recurring_event=match)
                linked += 1
            else:
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(f'Done — {linked} linked, {skipped} no match{"  (dry run)" if dry_run else ""}')
        )
