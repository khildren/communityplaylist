"""
dedup_events — find and merge duplicate events imported from multiple feeds.

Two events are considered duplicates when:
  • Same normalized title (& / and / + variants collapsed)
  • Start times within 4 hours of each other

Strategy: keep the event with the richer record (longer description OR
canonical venue location), migrate M2M relations to the keeper, delete the dupe.

Run:
    python manage.py dedup_events --dry-run    # show what would merge
    python manage.py dedup_events              # apply merges
"""
import re
from datetime import timedelta
from django.core.management.base import BaseCommand
from events.models import Event


def _norm(title):
    t = title.lower().strip()
    t = re.sub(r'\s*&\s*', ' and ', t)
    t = re.sub(r'\s*\+\s*', ' and ', t)
    t = re.sub(r"[''`]", '', t)
    t = re.sub(r'[^\w\s]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def _score(ev):
    """Higher is better — use to pick the keeper."""
    score = 0
    score += len(ev.description or '') // 10
    score += len(ev.location or '') * 2   # full address > bare name
    if ev.photo:
        score += 50
    if ev.website and not ev.website.startswith('http') and len(ev.website) < 80:
        score -= 10  # raw UID in website field, not a real URL — slight penalty
    return score


class Command(BaseCommand):
    help = 'Merge duplicate events imported from overlapping feeds.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report duplicates without deleting anything')
        parser.add_argument('--window-hours', type=int, default=4,
                            help='Max hours between start times to consider a duplicate (default 4)')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        window  = timedelta(hours=options['window_hours'])

        self.stdout.write('Loading events…')
        # Only consider approved / pending events — skip rejected ones
        events = list(
            Event.objects.filter(status__in=['approved', 'pending'])
            .order_by('start_date')
            .only('id', 'title', 'start_date', 'description', 'location',
                  'website', 'status')
        )
        self.stdout.write(f'  {len(events)} events to scan')

        # Group by normalized title
        from collections import defaultdict
        by_norm = defaultdict(list)
        for ev in events:
            by_norm[_norm(ev.title)].append(ev)

        merged = deleted = 0

        for norm_title, group in by_norm.items():
            if len(group) < 2:
                continue

            # Sort by start_date to find close pairs
            group.sort(key=lambda e: e.start_date)

            i = 0
            while i < len(group):
                j = i + 1
                while j < len(group):
                    a, b = group[i], group[j]
                    if abs(a.start_date - b.start_date) <= window:
                        # Duplicate pair found
                        keeper = a if _score(a) >= _score(b) else b
                        dupe   = b if keeper is a else a

                        self.stdout.write(
                            f'  DUP: "{a.title}" ({a.id}) ≈ "{b.title}" ({b.id})\n'
                            f'       keep={keeper.id}  delete={dupe.id}  '
                            f'Δt={abs(a.start_date - b.start_date)}'
                        )

                        if not dry_run:
                            # Migrate M2M: artists, genres, promoters
                            keeper_ev = Event.objects.get(pk=keeper.id)
                            dupe_ev   = Event.objects.get(pk=dupe.id)

                            keeper_ev.artists.add(*dupe_ev.artists.all())
                            keeper_ev.genres.add(*dupe_ev.genres.all())
                            keeper_ev.promoters.add(*dupe_ev.promoters.all())

                            # Supplement keeper description if dupe has more
                            if (len(dupe_ev.description or '') >
                                    len(keeper_ev.description or '')):
                                keeper_ev.description = dupe_ev.description
                                keeper_ev.save(update_fields=['description'])

                            # Supplement keeper photo if missing
                            if not keeper_ev.photo and dupe_ev.photo:
                                keeper_ev.photo = dupe_ev.photo
                                keeper_ev.save(update_fields=['photo'])

                            dupe_ev.delete()
                            deleted += 1

                        # Remove dupe from remaining candidates in group
                        group.pop(j)
                        merged += 1
                    else:
                        j += 1
                i += 1

        verb = 'Would merge' if dry_run else 'Merged'
        self.stdout.write(f'\n{verb} {merged} duplicate pairs ({deleted} events deleted).')
