"""
management command: python manage.py seed_food_pdx [--dry-run]

Seeds Portland farmers markets and food-access venues as RecurringEvent records.
The daily generate_recurring_events cron picks these up and creates Event instances
automatically up to lookahead_weeks ahead.

Safe to re-run — skips any title that already exists.
"""
from datetime import time

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from events.models import RecurringEvent

# ─── DATA ────────────────────────────────────────────────────────────────────
# Each entry:  (title, location, description, website, frequency, day_of_week,
#               week_of_month, start_time, duration_min, is_free, notes_for_desc)
#
# day_of_week: 0=Mon 1=Tue 2=Wed 3=Thu 4=Fri 5=Sat 6=Sun
# frequency:   weekly | monthly_weekday | daily

WEEKLY = RecurringEvent.FREQ_WEEKLY

FARMERS_MARKETS = [
    dict(
        title="Portland Farmers Market at PSU",
        location="SW Park Ave & SW Montgomery St, Portland, OR 97201",
        description=(
            "Portland's flagship year-round farmers market in the South Park Blocks "
            "at Portland State University. Local produce, meat, dairy, flowers, "
            "prepared food, and artisan goods from Oregon and SW Washington farms. "
            "Running since 1992 — rain or shine.\n\nSaturdays, year-round. "
            "April–October 8:30 AM–2 PM · November–March 9 AM–2 PM."
        ),
        website="https://www.portlandfarmersmarket.org",
        frequency=WEEKLY,
        day_of_week=5,  # Saturday
        start_time=time(8, 30),
        duration_minutes=330,  # 8:30am–2pm
        is_free=True,
    ),
    dict(
        title="Portland Farmers Market at Shemanski Park",
        location="SW Park Ave & SW Salmon St, Portland, OR 97205",
        description=(
            "A mid-week market in the heart of downtown Portland, tucked into "
            "Shemanski Park (South Park Blocks). Great for a lunch-hour shop. "
            "Seasonal: Wednesdays, May through October, 10 AM–2 PM."
        ),
        website="https://www.portlandfarmersmarket.org",
        frequency=WEEKLY,
        day_of_week=2,  # Wednesday
        start_time=time(10, 0),
        duration_minutes=240,
        is_free=True,
    ),
    dict(
        title="Hollywood Farmers' Market",
        location="NE Hancock St & NE 44th Ave, Portland, OR 97213",
        description=(
            "Northeast Portland's beloved neighborhood market, held at the Hollywood "
            "Transit Center plaza. Local farms, food artisans, and live music in a "
            "walkable, dog-friendly setting. Saturdays, May through October, 8 AM–1 PM."
        ),
        website="https://www.hollywoodfarmersmarket.org",
        frequency=WEEKLY,
        day_of_week=5,  # Saturday
        start_time=time(8, 0),
        duration_minutes=300,
        is_free=True,
    ),
    dict(
        title="Hillsdale Farmers' Market",
        location="SW Sunset Blvd & SW 16th Ave, Portland, OR 97239",
        description=(
            "Southwest Portland's community-run farmers market near Hillsdale Town "
            "Center. Vegetables, fruit, eggs, honey, baked goods, and flowers from "
            "local farms. Sundays, May through October, 10 AM–2 PM."
        ),
        website="https://www.hillsdalefarmersmarket.com",
        frequency=WEEKLY,
        day_of_week=6,  # Sunday
        start_time=time(10, 0),
        duration_minutes=240,
        is_free=True,
    ),
    dict(
        title="Montavilla Farmers Market",
        location="SE 76th Ave & SE Stark St, Portland, OR 97215",
        description=(
            "East Portland's neighborhood market in the Montavilla Town Center. "
            "A community gathering point with local produce, prepared food, and "
            "crafts. Sundays, May through October, 10 AM–2 PM."
        ),
        website="https://montavillamarket.org",
        frequency=WEEKLY,
        day_of_week=6,  # Sunday
        start_time=time(10, 0),
        duration_minutes=240,
        is_free=True,
    ),
    dict(
        title="Lents International Farmers Market",
        location="SE 92nd Ave & SE Woodstock Blvd, Portland, OR 97266",
        description=(
            "Celebrating the diverse cultures of outer SE Portland — one of the "
            "city's most multicultural markets. International produce, food, and "
            "vendors reflecting the Lents neighborhood community. "
            "Sundays, June through October, 9 AM–2 PM."
        ),
        website="https://lentsfarmersmarket.com",
        frequency=WEEKLY,
        day_of_week=6,  # Sunday
        start_time=time(9, 0),
        duration_minutes=300,
        is_free=True,
    ),
    dict(
        title="People's Farmers Market",
        location="N Mississippi Ave & N Shaver St, Portland, OR 97227",
        description=(
            "A Thursday afternoon market on North Mississippi Ave, the heart of "
            "Portland's vibrant Mississippi neighborhood. Local produce, prepared "
            "food, and community. Thursdays, May through October, 3–7 PM."
        ),
        website="https://www.peoples.coop",
        frequency=WEEKLY,
        day_of_week=3,  # Thursday
        start_time=time(15, 0),
        duration_minutes=240,
        is_free=True,
    ),
    dict(
        title="King Farmers Market",
        location="NE 7th Ave & NE Wygant St, Portland, OR 97211",
        description=(
            "A neighborhood market in the King School park, bringing fresh food "
            "and community together in inner NE Portland. Wednesdays, "
            "May through November, 3–7 PM."
        ),
        website="https://www.portlandfarmersmarket.org",
        frequency=WEEKLY,
        day_of_week=2,  # Wednesday
        start_time=time(15, 0),
        duration_minutes=240,
        is_free=True,
    ),
    dict(
        title="Kenton Farmers Market",
        location="N Denver Ave & N McClellan St, Portland, OR 97217",
        description=(
            "North Portland's weekly community market in the walkable Kenton "
            "neighborhood. Local farms, food carts, and neighbors. "
            "Thursdays, June through September, 3–7 PM."
        ),
        website="https://www.kentonfarmersmarket.com",
        frequency=WEEKLY,
        day_of_week=3,  # Thursday
        start_time=time(15, 0),
        duration_minutes=240,
        is_free=True,
    ),
    dict(
        title="Moreland Farmers Market",
        location="SE Bybee Blvd & SE 14th Ave, Portland, OR 97202",
        description=(
            "A beloved Southeast Portland neighborhood market in Sellwood–Moreland. "
            "Wednesdays, May through October, 3–7 PM."
        ),
        website="https://www.morelandfarmersmarket.org",
        frequency=WEEKLY,
        day_of_week=2,  # Wednesday
        start_time=time(15, 0),
        duration_minutes=240,
        is_free=True,
    ),
]

FOOD_ACCESS = [
    dict(
        title="Blanchet House — Free Breakfast",
        location="310 NW Glisan St, Portland, OR 97209",
        description=(
            "Blanchet House of Hospitality serves a free hot breakfast every "
            "morning — no questions asked, no ID required. One of Portland's "
            "longest-running hospitality missions, feeding anyone who shows up. "
            "Daily, 6:30–8:30 AM. Lunch also served Mon–Fri 11 AM–12 PM."
        ),
        website="https://www.blanchethouse.org",
        frequency=RecurringEvent.FREQ_DAILY,
        day_of_week=None,
        start_time=time(6, 30),
        duration_minutes=120,
        is_free=True,
    ),
    dict(
        title="Potluck in the Park",
        location="Tom McCall Waterfront Park, Portland, OR 97204",
        description=(
            "Community-organized outdoor free meal on the Portland waterfront. "
            "Open to anyone. Bring food to share if you can, eat if you need. "
            "Sundays, April through October, weather permitting, 1–3 PM."
        ),
        website="https://www.potluckinthepark.org",
        frequency=WEEKLY,
        day_of_week=6,  # Sunday
        start_time=time(13, 0),
        duration_minutes=120,
        is_free=True,
    ),
    dict(
        title="Oregon Food Bank — SE Portland Distribution",
        location="7900 NE 33rd Dr, Portland, OR 97211",
        description=(
            "Oregon Food Bank's main Portland distribution hub. Free groceries "
            "for individuals and families — no income verification or ID required. "
            "Check oregonfoodbank.org for current hours and what to bring."
        ),
        website="https://www.oregonfoodbank.org",
        frequency=WEEKLY,
        day_of_week=3,  # Thursday
        start_time=time(9, 0),
        duration_minutes=240,
        is_free=True,
    ),
    dict(
        title="St. Francis of Assisi Pantry",
        location="1131 SE Oakland Ave, Portland, OR 97202",
        description=(
            "Free food pantry open to all SE Portland residents. No appointment "
            "needed. Operated by St. Francis parish and community volunteers. "
            "Saturdays 9 AM–12 PM."
        ),
        website="https://stfrancispdx.org",
        frequency=WEEKLY,
        day_of_week=5,  # Saturday
        start_time=time(9, 0),
        duration_minutes=180,
        is_free=True,
    ),
    dict(
        title="JOIN — Street Outreach Meals",
        location="Portland, OR (rotating locations)",
        description=(
            "JOIN provides meals and services for Portlanders experiencing "
            "homelessness through street outreach teams. Contact JOIN for "
            "current meal locations. joinus.org"
        ),
        website="https://www.joinus.org",
        frequency=WEEKLY,
        day_of_week=2,  # Wednesday
        start_time=time(17, 0),
        duration_minutes=120,
        is_free=True,
    ),
    dict(
        title="Outside In — Drop-In Meals for Youth",
        location="1132 SW 13th Ave, Portland, OR 97205",
        description=(
            "Outside In serves young people (14–24) and young adults experiencing "
            "homelessness. Drop-in meals and basic services Monday–Friday. "
            "Lunch 12–1 PM."
        ),
        website="https://www.outsidein.org",
        frequency=WEEKLY,
        day_of_week=0,  # Monday (represents Mon–Fri — create as weekly anchor)
        start_time=time(12, 0),
        duration_minutes=60,
        is_free=True,
    ),
    dict(
        title="Central City Concern — Free Community Meals",
        location="232 NW 6th Ave, Portland, OR 97209",
        description=(
            "Central City Concern operates meal programs for people experiencing "
            "homelessness and poverty in downtown Portland. Open daily; see "
            "centralcityconcern.org for current schedule."
        ),
        website="https://www.centralcityconcern.org",
        frequency=WEEKLY,
        day_of_week=0,  # Monday anchor
        start_time=time(8, 0),
        duration_minutes=60,
        is_free=True,
    ),
    dict(
        title="The Rosehip Medic Collective — Community Fridge",
        location="N Williams Ave, Portland, OR 97227",
        description=(
            "Community-run free fridge stocked daily by neighbors. Take what you "
            "need, leave what you can. Part of the mutual aid network keeping "
            "Portland fed and connected."
        ),
        website="https://www.portlandmutualaid.org",
        frequency=RecurringEvent.FREQ_DAILY,
        day_of_week=None,
        start_time=time(8, 0),
        duration_minutes=480,  # open all day
        is_free=True,
    ),
]
# ─────────────────────────────────────────────────────────────────────────────


class Command(BaseCommand):
    help = 'Seed Portland farmers markets and food-access events as RecurringEvents'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print what would be created without saving',
        )

    def handle(self, *args, **options):
        dry = options['dry_run']
        if dry:
            self.stdout.write('DRY RUN — nothing will be saved\n')

        created_count = skipped_count = 0

        all_entries = [
            ('farmers market', FARMERS_MARKETS),
            ('food access',    FOOD_ACCESS),
        ]

        for group_name, entries in all_entries:
            self.stdout.write(f'\n── {group_name.upper()} ──')
            for entry in entries:
                title = entry['title']

                if RecurringEvent.objects.filter(title=title).exists():
                    self.stdout.write(f'  skip  {title}')
                    skipped_count += 1
                    continue

                if dry:
                    freq = entry['frequency']
                    dow  = entry.get('day_of_week')
                    day_names = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
                    day_str = day_names[dow] if dow is not None else 'daily'
                    self.stdout.write(
                        f'  would create  [{day_str}] {title}'
                    )
                    created_count += 1
                    continue

                rec = RecurringEvent(
                    title=title,
                    location=entry['location'],
                    description=entry['description'],
                    website=entry.get('website', ''),
                    category='food',
                    is_free=entry.get('is_free', True),
                    frequency=entry['frequency'],
                    day_of_week=entry.get('day_of_week'),
                    start_time=entry['start_time'],
                    duration_minutes=entry.get('duration_minutes', 120),
                    interval=1,
                    lookahead_weeks=16,
                    auto_approve=True,
                    active=True,
                    submitted_by='seed_food_pdx',
                )
                rec.save()
                self.stdout.write(f'  ✓ created  {title}')
                created_count += 1

        action = 'Would create' if dry else 'Created'
        self.stdout.write(self.style.SUCCESS(
            f'\n{action} {created_count} recurring events, skipped {skipped_count} existing.'
        ))
        if not dry:
            self.stdout.write(
                'generate_recurring_events will start producing instances at next run (6:05 AM).'
            )
