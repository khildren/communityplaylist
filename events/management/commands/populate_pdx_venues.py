"""
management command: python manage.py populate_pdx_venues

Seeds the VenueFeed table with known Portland, OR event sources.
Safe to re-run — skips entries that already exist by name.

Sources included:
  - Calagator (PDX tech/community open calendar)
  - Songkick Portland metro (concerts at all PDX venues)
  - Individual venue iCal / Eventbrite feeds
  - City of Portland events
  - Eventbrite PDX-wide search (requires EVENTBRITE_API_KEY in settings)
"""
from django.core.management.base import BaseCommand
from events.models import VenueFeed

PDX_VENUES = [
    # ── Aggregators — broad net, highest value ──
    {
        'name': 'Calagator — PDX Tech & Community',
        'website': 'https://calagator.org',
        'url': 'https://calagator.org/events.ics',
        'source_type': 'ical',
        'auto_approve': False,
        'default_category': '',
        'notes': 'Open-source Portland community calendar. Strong tech/civic/community events. Very reliable iCal feed.',
    },
    {
        'name': 'Songkick — Portland Metro Concerts',
        'website': 'https://www.songkick.com/metro-areas/28760-us-portland',
        'url': 'https://www.songkick.com/metro-areas/28760-us-portland/calendar.ics',
        'source_type': 'ical',
        'auto_approve': False,
        'default_category': 'music',
        'notes': 'Covers concerts across all Portland venues tracked by Songkick. High volume.',
    },
    {
        'name': 'Eventbrite — Portland (API)',
        'website': 'https://www.eventbrite.com',
        'url': '',
        'source_type': 'eventbrite',
        'auto_approve': False,
        'default_category': '',
        'notes': 'Broad Eventbrite search for Portland events within 25mi. Requires EVENTBRITE_API_KEY in Django settings.',
        'active': False,  # disabled until API key configured
    },

    # ── Music venues ──
    {
        'name': 'Mississippi Studios',
        'website': 'https://www.mississippistudios.com',
        'url': 'https://www.mississippistudios.com/events/list/?ical=1',
        'source_type': 'ical',
        'auto_approve': False,
        'default_category': 'music',
        'notes': 'Intimate N Portland music venue. WordPress iCal — verify URL is active.',
    },
    {
        'name': 'Doug Fir Lounge',
        'website': 'https://www.dougfirlounge.com',
        'url': 'https://www.dougfirlounge.com/events/list/?ical=1',
        'source_type': 'ical',
        'auto_approve': False,
        'default_category': 'music',
        'notes': 'SE Portland music venue + restaurant. WordPress iCal — verify URL is active.',
    },
    {
        'name': 'Wonder Ballroom',
        'website': 'https://www.wonderballroom.com',
        'url': 'https://www.wonderballroom.com/events/list/?ical=1',
        'source_type': 'ical',
        'auto_approve': False,
        'default_category': 'music',
        'notes': 'NE Portland all-ages venue. WordPress iCal — verify URL.',
    },
    {
        'name': 'Revolution Hall',
        'website': 'https://www.revolutionhall.com',
        'url': 'https://www.revolutionhall.com/events/list/?ical=1',
        'source_type': 'ical',
        'auto_approve': False,
        'default_category': 'music',
        'notes': 'Historic school turned music venue SE Portland.',
    },
    {
        'name': 'Alberta Rose Theatre',
        'website': 'https://www.albertarosetheatre.com',
        'url': 'https://www.albertarosetheatre.com/events/list/?ical=1',
        'source_type': 'ical',
        'auto_approve': False,
        'default_category': 'music',
        'notes': 'NE Alberta neighborhood arts venue.',
    },
    {
        'name': "Dante's",
        'website': 'https://www.danteslive.com',
        'url': 'https://www.danteslive.com/events/list/?ical=1',
        'source_type': 'ical',
        'auto_approve': False,
        'default_category': 'music',
        'notes': 'Downtown PDX rock/burlesque/events venue.',
    },
    {
        'name': 'Holocene',
        'website': 'https://www.holocene.org',
        'url': 'https://www.holocene.org/events/list/?ical=1',
        'source_type': 'ical',
        'auto_approve': False,
        'default_category': 'music',
        'notes': 'SE Portland electronic/dance/arts venue.',
    },
    {
        'name': 'Aladdin Theater',
        'website': 'https://www.aladdin-theater.com',
        'url': 'https://www.aladdin-theater.com/events/list/?ical=1',
        'source_type': 'ical',
        'auto_approve': False,
        'default_category': 'music',
        'notes': 'SE Division historic theater.',
    },
    {
        'name': 'The Goodfoot',
        'website': 'https://www.thegoodfoot.com',
        'url': 'https://www.thegoodfoot.com/events/list/?ical=1',
        'source_type': 'ical',
        'auto_approve': False,
        'default_category': 'music',
        'notes': 'SE PDX dive bar with live music + dance nights.',
    },
    {
        'name': "McMenamins — All PDX Venues",
        'website': 'https://www.mcmenamins.com/events',
        'url': 'https://www.mcmenamins.com/events?format=ical',
        'source_type': 'ical',
        'auto_approve': False,
        'default_category': '',
        'notes': 'McMenamins covers many PDX venues (Crystal, Edgefield, Kennedy School, etc). Check their /events page for the actual iCal export URL.',
    },

    # ── Arts & culture ──
    {
        'name': "Portland'5 Centers for the Arts",
        'website': 'https://portland5.com',
        'url': 'https://portland5.com/events/list/?ical=1',
        'source_type': 'ical',
        'auto_approve': False,
        'default_category': '',
        'notes': 'Operates Arlene Schnitzer, Keller Auditorium, Newmark, etc. Verify iCal URL on their site.',
    },
    {
        'name': 'Portland Art Museum',
        'website': 'https://portlandartmuseum.org',
        'url': 'https://portlandartmuseum.org/events/?ical=1',
        'source_type': 'ical',
        'auto_approve': False,
        'default_category': '',
        'notes': 'Exhibitions, talks, film screenings.',
    },
    {
        'name': 'Portland Center Stage',
        'website': 'https://www.pcs.org',
        'url': 'https://www.pcs.org/events/list/?ical=1',
        'source_type': 'ical',
        'auto_approve': False,
        'default_category': '',
        'notes': 'Theater at the Armory, Pearl District.',
    },
    {
        'name': 'Oregon Museum of Science and Industry (OMSI)',
        'website': 'https://www.omsi.edu',
        'url': 'https://www.omsi.edu/events?ical=1',
        'source_type': 'ical',
        'auto_approve': False,
        'default_category': '',
        'notes': 'Science events, adult events, camps. Verify iCal URL.',
    },

    # ── Community / outdoors ──
    {
        'name': 'City of Portland — Parks & Recreation Events',
        'website': 'https://www.portland.gov/parks/events',
        'url': 'https://www.portland.gov/parks/events?ical=1',
        'source_type': 'ical',
        'auto_approve': False,
        'default_category': '',
        'notes': 'Free/low-cost public events in city parks. Verify iCal export URL.',
    },
    {
        'name': 'Bicycle Transportation Alliance / BTA',
        'website': 'https://btaoregon.org',
        'url': 'https://btaoregon.org/events/list/?ical=1',
        'source_type': 'ical',
        'auto_approve': False,
        'default_category': 'bike',
        'notes': 'Bike advocacy events, rides, community meetings.',
    },
    {
        'name': 'PDX Pedals / Shift Bike Events',
        'website': 'https://shift2bikes.org',
        'url': 'https://www.shift2bikes.org/cal/vevent-rss.php',
        'source_type': 'ical',
        'auto_approve': False,
        'default_category': 'bike',
        'notes': 'Shift to Bikes community calendar — free PDX bike events. Check if URL returns iCal or RSS.',
    },
    {
        'name': 'Portland Farmers Market',
        'website': 'https://www.portlandfarmersmarket.org',
        'url': 'https://www.portlandfarmersmarket.org/events/list/?ical=1',
        'source_type': 'ical',
        'auto_approve': False,
        'default_category': 'food',
        'notes': 'Saturday and weekday markets at PSU, Shemanski Park, etc.',
    },
    {
        'name': 'Portland Night Market',
        'website': 'https://www.portlandnightmarket.com',
        'url': 'https://www.portlandnightmarket.com/events/list/?ical=1',
        'source_type': 'ical',
        'auto_approve': False,
        'default_category': 'food',
        'notes': 'Asian-inspired night market events.',
    },
]


class Command(BaseCommand):
    help = 'Seed VenueFeed with curated Portland, OR event sources'

    def handle(self, *args, **options):
        added = skipped = 0
        for data in PDX_VENUES:
            active = data.pop('active', True)  # default active unless explicitly False
            name = data['name']
            if VenueFeed.objects.filter(name=name).exists():
                self.stdout.write(f'  skip (exists): {name}')
                skipped += 1
                continue
            VenueFeed.objects.create(active=active, **data)
            status = 'DISABLED' if not active else 'active'
            self.stdout.write(f'  + [{status}] {name}')
            added += 1

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. {added} venues added, {skipped} already existed.'
        ))
        self.stdout.write(
            '\nIMPORTANT: Many iCal URLs above are best-guess WordPress/CMS patterns.\n'
            'Visit each venue site to confirm their actual iCal export URL,\n'
            'then run: python manage.py import_venue_feeds --feed <ID>\n'
            'to test each feed before enabling auto-run.'
        )
