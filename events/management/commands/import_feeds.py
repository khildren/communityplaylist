"""
management command: python manage.py import_feeds

Pulls events from all user-added iCal feed URLs, creates Event records
(status=pending) attributed to that user. De-duplicates by UID+slug.
Run twice a week via cron:
  0 6 * * 1,4  /path/to/venv/bin/python /path/to/manage.py import_feeds
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify
from events.models import CalendarFeed, Event
import requests
from icalendar import Calendar
from datetime import datetime, date
import pytz


PDX_TZ = pytz.timezone('America/Los_Angeles')


def to_aware(dt):
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return PDX_TZ.localize(dt)
        return dt.astimezone(PDX_TZ)
    if isinstance(dt, date):
        return PDX_TZ.localize(datetime(dt.year, dt.month, dt.day, 0, 0))
    return None


class Command(BaseCommand):
    help = 'Import events from user calendar feeds'

    def handle(self, *args, **options):
        feeds = CalendarFeed.objects.select_related('user').all()
        now = timezone.now()
        created_total = 0
        skipped_total = 0

        for feed in feeds:
            self.stdout.write(f'Fetching {feed.url} for {feed.user.email}…')
            try:
                r = requests.get(feed.url, timeout=10, headers={'User-Agent': 'CommunityPlaylist/1.0'})
                cal = Calendar.from_ical(r.content)
            except Exception as e:
                self.stderr.write(f'  ERROR: {e}')
                continue

            created = 0
            skipped = 0
            for component in cal.walk():
                if component.name != 'VEVENT':
                    continue
                try:
                    summary  = str(component.get('summary', '')).strip()
                    uid      = str(component.get('uid', ''))
                    dtstart  = to_aware(component.get('dtstart').dt)
                    dtend_raw = component.get('dtend')
                    dtend    = to_aware(dtend_raw.dt) if dtend_raw else None
                    desc     = str(component.get('description', '')).strip()
                    location = str(component.get('location', '')).strip()

                    if not summary or not dtstart:
                        continue
                    # Skip past events
                    if dtstart < now:
                        continue
                    # De-duplicate by uid stored in website field or by title+date slug
                    slug_base = slugify(f"{summary}-{dtstart.strftime('%Y-%m-%d')}")
                    if Event.objects.filter(slug__startswith=slug_base).exists():
                        skipped += 1
                        continue

                    Event.objects.create(
                        title=summary[:200],
                        description=desc[:2000] or f'Imported from {feed.label or feed.url}',
                        location=location[:300],
                        start_date=dtstart,
                        end_date=dtend,
                        submitted_user=feed.user,
                        submitted_by=feed.user.email,
                        submitted_email=feed.user.email,
                        status='pending',
                        is_free=True,
                    )
                    created += 1
                except Exception as e:
                    self.stderr.write(f'  skipping event: {e}')
                    continue

            feed.last_synced = now
            feed.save(update_fields=['last_synced'])
            self.stdout.write(f'  ✓ {created} created, {skipped} skipped (already exist)')
            created_total += created
            skipped_total += skipped

        self.stdout.write(self.style.SUCCESS(
            f'Done. {created_total} events imported, {skipped_total} skipped.'
        ))
