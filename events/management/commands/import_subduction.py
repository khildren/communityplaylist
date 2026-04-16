import requests, re
import recurring_ical_events
from icalendar import Calendar
from django.core.management.base import BaseCommand
from datetime import datetime, date
import pytz
from events.models import Event, PromoterProfile

URL = "https://calendar.google.com/calendar/ical/132cc752f64ce93a8987d53b695838ed237c2a7072615ef0361f68b247036561%40group.calendar.google.com/public/basic.ics"

# Promoter prefix → PromoterProfile name mapping
# When a calendar SUMMARY starts with one of these prefixes, auto-link the event.
PROMOTER_PREFIXES = {
    'gnosis': 'Gnosis Crew',
}

def _clean_title(title):
    """Strip trailing colon/whitespace: 'Gnosis:' → 'Gnosis'."""
    return re.sub(r'[\s:]+$', '', title).strip()

def _get_promoter_cache():
    cache = {}
    for name in PROMOTER_PREFIXES.values():
        try:
            cache[name] = PromoterProfile.objects.get(name=name)
        except PromoterProfile.DoesNotExist:
            pass
    return cache

class Command(BaseCommand):
    help = "Import Subduction Audio Google Calendar into Community Playlist events"

    def handle(self, *a, **k):
        r = requests.get(URL, headers={"User-Agent": "CommunityPlaylist/1.0"})
        cal = Calendar.from_ical(r.content)
        now = datetime.now(pytz.UTC)
        end = datetime(2027, 1, 1, tzinfo=pytz.UTC)
        events = recurring_ical_events.of(cal).between(now, end)
        promoter_cache = _get_promoter_cache()
        created = updated = linked = 0

        for c in events:
            raw_title = str(c.get("SUMMARY", "")).strip()
            if not raw_title:
                continue

            title = _clean_title(raw_title)
            if not title:
                continue

            # Parse start date
            v = c.get("DTSTART").dt
            if isinstance(v, datetime):
                st = v if v.tzinfo else pytz.UTC.localize(v)
                st = st.astimezone(pytz.UTC)
            elif isinstance(v, date):
                st = datetime(v.year, v.month, v.day, 19, 0, tzinfo=pytz.UTC)
            else:
                continue

            uid  = str(c.get("UID", "")).strip()
            loc  = str(c.get("LOCATION", "Portland, OR")).strip() or "Portland, OR"
            desc = re.sub(r"<[^>]+>", "", str(c.get("DESCRIPTION", ""))).strip()
            url  = str(c.get("URL", "")).strip()

            # ── Deduplicate by UID (stored in website field when no URL present) ──
            # Priority: UID match → (title, start_date) fallback for old records
            obj = None
            if uid:
                obj = Event.objects.filter(website=uid, start_date=st).first()
                if not obj:
                    # Check if old record exists under bare title (e.g. "Gnosis")
                    # with same date — update it rather than creating a duplicate
                    bare = title.split(':')[0].strip()
                    obj = Event.objects.filter(
                        title__istartswith=bare, start_date=st,
                        submitted_by="Community Playlist"
                    ).first()

            if obj:
                # Update in place — title may have filled in since last import
                obj.title       = title[:200]
                obj.description = desc
                obj.location    = loc
                if uid and not url:
                    obj.website = uid  # store UID for future dedup
                elif url:
                    obj.website = url
                obj.save()
                was_created = False
            else:
                obj, was_created = Event.objects.get_or_create(
                    title=title[:200],
                    start_date=st,
                    defaults=dict(
                        description=desc,
                        location=loc,
                        website=uid if (uid and not url) else url,
                        submitted_by="Community Playlist",
                        status="approved",
                    )
                )

            if was_created:
                created += 1
            else:
                updated += 1

            # ── Auto-link to promoter based on title prefix ──
            title_lower = title.lower()
            for prefix, promoter_name in PROMOTER_PREFIXES.items():
                if title_lower.startswith(prefix):
                    promoter = promoter_cache.get(promoter_name)
                    if promoter and promoter not in obj.promoters.all():
                        obj.promoters.add(promoter)
                        linked += 1

        self.stdout.write(
            f"Done — {created} created, {updated} updated, {linked} promoter links added"
        )
