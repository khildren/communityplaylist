import requests, re
import recurring_ical_events
from icalendar import Calendar
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import datetime, date
import pytz
from events.models import Event

URL = "https://calendar.google.com/calendar/ical/132cc752f64ce93a8987d53b695838ed237c2a7072615ef0361f68b247036561%40group.calendar.google.com/public/basic.ics"

class Command(BaseCommand):
    help = "Import from Google Calendar"

    def handle(self, *a, **k):
        r = requests.get(URL, headers={"User-Agent": "CommunityPlaylist/1.0"})
        cal = Calendar.from_ical(r.content)
        now = datetime.now(pytz.UTC)
        end = datetime(2027, 1, 1, tzinfo=pytz.UTC)
        events = recurring_ical_events.of(cal).between(now, end)
        created = 0
        updated = 0

        for c in events:
            title = str(c.get("SUMMARY", "")).strip()
            if not title:
                continue
            v = c.get("DTSTART").dt
            if isinstance(v, datetime):
                st = v if v.tzinfo else pytz.UTC.localize(v)
                st = st.astimezone(pytz.UTC)
            elif isinstance(v, date):
                st = datetime(v.year, v.month, v.day, 19, 0, tzinfo=pytz.UTC)
            else:
                continue

            loc = str(c.get("LOCATION", "Portland, OR")).strip() or "Portland, OR"
            desc = re.sub("<[^>]+>", "", str(c.get("DESCRIPTION", ""))).strip()
            url = str(c.get("URL", "")).strip()

            obj, was_created = Event.objects.update_or_create(
                title=title,
                start_date=st,
                defaults=dict(
                    description=desc,
                    location=loc,
                    website=url,
                    submitted_by="Community Playlist",
                    status="approved"
                )
            )
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(f"Done - {created} created, {updated} updated")
