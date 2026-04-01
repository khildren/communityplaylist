"""
management command: python manage.py generate_recurring_events

For each active RecurringEvent, generates Event instances up to
`lookahead_weeks` ahead. Safe to run daily — skips dates that already
have an instance.

Cron (daily 6 AM):
  0 6 * * *  /path/venv/bin/python /path/manage.py generate_recurring_events
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.timezone import localtime
from django.utils.text import slugify
from datetime import datetime, timedelta
import pytz

PDX_TZ = pytz.timezone('America/Los_Angeles')


class Command(BaseCommand):
    help = 'Generate upcoming Event instances from RecurringEvent templates'

    def handle(self, *args, **options):
        from events.models import RecurringEvent, Event
        from events.enrich import enrich_event
        from events.geocode import NominatimRateLimited

        now      = localtime(timezone.now())
        today    = now.date()
        created_total = 0

        from board.models import Topic

        for rec in RecurringEvent.objects.filter(active=True):
            # Auto-create a board thread for this recurring event if none exists yet
            if not Topic.objects.filter(recurring_event=rec).exists():
                Topic.objects.create(
                    title=f"{rec.title} — recurring event thread",
                    body=(
                        f"{rec.description}\n\n"
                        f"Community thread for: {rec.title}\n"
                        f"Location: {rec.location}"
                    ),
                    author_name='Community Playlist',
                    category='general',
                    recurring_event=rec,
                )
            lookahead = today + timedelta(weeks=rec.lookahead_weeks)
            dates     = rec.next_dates(today, count=200)
            dates     = [d for d in dates if d <= lookahead]

            for d in dates:
                # Build timezone-aware start datetime in PDX
                start_naive = datetime.combine(d, rec.start_time)
                start_dt    = PDX_TZ.localize(start_naive)

                if rec.duration_minutes:
                    end_dt = start_dt + timedelta(minutes=rec.duration_minutes)
                else:
                    end_dt = None

                # Skip if an instance already exists for this recurring event on this date
                if Event.objects.filter(
                    recurring_event=rec,
                    start_date__date=d,
                ).exists():
                    continue

                status = 'approved' if rec.auto_approve else 'pending'

                # Unique slug
                slug_base = slugify(f"{rec.title}-{d.strftime('%Y-%m-%d')}")
                slug, n = slug_base, 1
                while Event.objects.filter(slug=slug).exists():
                    slug = f"{slug_base}-{n}"; n += 1

                # Auto-assign music category when genres present and no category set
                category = rec.category
                if not category and rec.genres.exists():
                    category = 'music'

                ev = Event.objects.create(
                    title           = rec.title,
                    slug            = slug,
                    description     = rec.description,
                    location        = rec.location,
                    category        = category,
                    is_free         = rec.is_free,
                    price_info      = rec.price_info,
                    website         = rec.website,
                    start_date      = start_dt,
                    end_date        = end_dt,
                    status          = status,
                    submitted_by    = rec.submitted_by or rec.title,
                    submitted_email = rec.submitted_email,
                    submitted_user  = rec.submitted_user,
                    recurring_event = rec,
                )

                # Copy photo reference from template if present
                if rec.photo:
                    ev.photo = rec.photo
                    ev.save(update_fields=['photo'])

                # Copy genres and residents→artists
                if rec.genres.exists():
                    ev.genres.set(rec.genres.all())
                if rec.residents.exists():
                    ev.artists.set(rec.residents.all())

                try:
                    enrich_event(ev, geocode=True, save=True)
                except NominatimRateLimited:
                    self.stdout.write('  ! Nominatim rate limit — skipping geocode for remaining events')
                    enrich_event(ev, geocode=False, save=True)
                created_total += 1
                self.stdout.write(f'  + {ev.title} — {d}')

        self.stdout.write(self.style.SUCCESS(
            f'Done. {created_total} recurring event instance(s) created.'
        ))
