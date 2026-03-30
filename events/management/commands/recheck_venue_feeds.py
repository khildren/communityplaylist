"""
management command: python manage.py recheck_venue_feeds

Tests all inactive iCal VenueFeeds to see if their URLs have started working.
Re-enables any that now return valid iCal data.

Run monthly via cron:
  0 8 1 * *  /path/venv/bin/python /path/manage.py recheck_venue_feeds >> /var/log/cp_recheck_feeds.log 2>&1
"""
from django.core.management.base import BaseCommand
from events.models import VenueFeed
from icalendar import Calendar
import requests


class Command(BaseCommand):
    help = 'Recheck inactive iCal feeds and re-enable any that are now working'

    def handle(self, *args, **options):
        inactive = VenueFeed.objects.filter(active=False, source_type='ical')
        self.stdout.write(f'Checking {inactive.count()} inactive iCal feeds…\n')

        reactivated = still_broken = 0

        for feed in inactive:
            if not feed.url:
                continue

            self.stdout.write(f'  {feed.name}… ', ending='')
            try:
                r = requests.get(
                    feed.url,
                    timeout=10,
                    headers={'User-Agent': 'CommunityPlaylist/1.0 (communityplaylist.com)'},
                )
                r.raise_for_status()
                Calendar.from_ical(r.content)  # validate it's actual iCal
                # It works — re-enable
                feed.active = True
                feed.last_error = ''
                feed.save(update_fields=['active', 'last_error'])
                self.stdout.write(self.style.SUCCESS('RESTORED ✓'))
                reactivated += 1
            except Exception as e:
                feed.last_error = f'recheck failed: {e}'
                feed.save(update_fields=['last_error'])
                self.stdout.write(f'still broken ({type(e).__name__})')
                still_broken += 1

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. {reactivated} re-enabled, {still_broken} still broken.'
        ))
