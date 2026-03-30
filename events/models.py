from django.db import models
from django.db.models import F
from django.utils.text import slugify


class CalendarFeed(models.Model):
    """A user-owned iCal feed URL the system imports events from."""
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='calendar_feeds')
    url  = models.URLField(max_length=500)
    label = models.CharField(max_length=100, blank=True, help_text='e.g. "My Google Calendar"')
    last_synced = models.DateTimeField(null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return self.label or self.url


class VenueFeed(models.Model):
    """Admin-managed event source — iCal feed or Eventbrite API query for a PDX venue/source."""
    SOURCE_ICAL        = 'ical'
    SOURCE_EVENTBRITE  = 'eventbrite'
    SOURCE_MUSICBRAINZ = 'musicbrainz'
    SOURCE_CHOICES = [
        (SOURCE_ICAL,        'iCal Feed'),
        (SOURCE_EVENTBRITE,  'Eventbrite API'),
        (SOURCE_MUSICBRAINZ, 'MusicBrainz API'),
    ]

    CATEGORY_CHOICES = [
        ('', 'Auto-detect'),
        ('music',  'Music'),
        ('bike',   'Bike'),
        ('fund',   'Fundraiser'),
        ('food',   'Food'),
        ('hybrid', 'Hybrid'),
    ]

    name            = models.CharField(max_length=200)
    website         = models.URLField(max_length=500, blank=True)
    url             = models.URLField(max_length=500, blank=True, help_text='iCal feed URL (required for iCal source)')
    source_type     = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_ICAL)
    active          = models.BooleanField(default=True)
    auto_approve    = models.BooleanField(default=False, help_text='Publish events immediately without manual review')
    default_category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, blank=True)
    default_genres   = models.ManyToManyField('Genre', blank=True, related_name='venue_feeds',
                                              help_text='Applied to imported events when category is Music')
    residents        = models.ManyToManyField('Artist', blank=True, related_name='resident_feeds',
                                              help_text='Regular/resident artists — auto-tagged on every imported event')
    last_synced     = models.DateTimeField(null=True, blank=True)
    last_error      = models.TextField(blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)
    notes           = models.TextField(blank=True, help_text='Internal notes about this source')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class SiteStats(models.Model):
    visit_count = models.BigIntegerField(default=0)

    class Meta:
        verbose_name_plural = 'site stats'

    @classmethod
    def record_visit(cls, request):
        if not request.session.get('cp_counted'):
            cls.objects.filter(pk=1).update(visit_count=F('visit_count') + 1)
            request.session['cp_counted'] = True

    @classmethod
    def get_count(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj.visit_count


class Genre(models.Model):
    name = models.CharField(max_length=100, unique=True)
    mb_id = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Artist(models.Model):
    name   = models.CharField(max_length=200, unique=True)
    mb_id  = models.CharField(max_length=100, blank=True, help_text='MusicBrainz artist ID')
    bio    = models.TextField(blank=True)
    website = models.URLField(blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class RecurringEvent(models.Model):
    """Template that auto-generates Event instances on a schedule."""

    FREQ_DAILY            = 'daily'
    FREQ_WEEKLY           = 'weekly'
    FREQ_MONTHLY_DATE     = 'monthly_date'
    FREQ_MONTHLY_WEEKDAY  = 'monthly_weekday'
    FREQ_ANNUALLY         = 'annually'
    FREQ_CHOICES = [
        (FREQ_DAILY,           'Daily'),
        (FREQ_WEEKLY,          'Weekly'),
        (FREQ_MONTHLY_DATE,    'Monthly (same date each month)'),
        (FREQ_MONTHLY_WEEKDAY, 'Monthly (e.g. every 3rd Thursday)'),
        (FREQ_ANNUALLY,        'Annually'),
    ]
    WEEKDAY_CHOICES = [(0,'Mon'),(1,'Tue'),(2,'Wed'),(3,'Thu'),(4,'Fri'),(5,'Sat'),(6,'Sun')]
    WEEK_CHOICES    = [(1,'1st'),(2,'2nd'),(3,'3rd'),(4,'4th'),(5,'Last')]

    CATEGORY_CHOICES = [
        ('music',  'Music'),
        ('arts',   'Arts & Comedy'),
        ('bike',   'Bike'),
        ('fund',   'Fundraiser'),
        ('food',   'Food'),
        ('hybrid', 'Hybrid'),
    ]

    # Core fields mirrored on generated Event instances
    title       = models.CharField(max_length=200)
    slug        = models.SlugField(max_length=220, unique=True, blank=True)
    description = models.TextField()
    location    = models.CharField(max_length=300)
    category    = models.CharField(max_length=20, choices=CATEGORY_CHOICES, blank=True)
    is_free     = models.BooleanField(default=True)
    price_info  = models.CharField(max_length=100, blank=True)
    website     = models.URLField(blank=True)
    photo       = models.ImageField(upload_to='recurring/', blank=True, null=True)

    # Schedule
    frequency       = models.CharField(max_length=20, choices=FREQ_CHOICES)
    interval        = models.PositiveIntegerField(default=1,
                        help_text='Every N units — e.g. 2 = bi-weekly')
    day_of_week     = models.IntegerField(null=True, blank=True, choices=WEEKDAY_CHOICES,
                        help_text='For weekly / monthly-weekday frequency')
    week_of_month   = models.IntegerField(null=True, blank=True, choices=WEEK_CHOICES,
                        help_text='1-5 for monthly-weekday (5 = last)')
    start_time      = models.TimeField()
    duration_minutes = models.PositiveIntegerField(default=120,
                        help_text='Duration in minutes (0 = no end time)')

    # Artists / genres
    residents = models.ManyToManyField('Artist', blank=True, related_name='recurring_events')
    genres    = models.ManyToManyField('Genre',  blank=True, related_name='recurring_events')

    # Admin
    active           = models.BooleanField(default=True)
    auto_approve     = models.BooleanField(default=False)
    lookahead_weeks  = models.PositiveIntegerField(default=12,
                        help_text='Generate instances this many weeks ahead')
    submitted_by    = models.CharField(max_length=100, blank=True)
    submitted_email = models.EmailField(blank=True)
    submitted_user  = models.ForeignKey('auth.User', null=True, blank=True,
                        on_delete=models.SET_NULL, related_name='recurring_events')
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['title']

    def __str__(self):
        return f"{self.title} ({self.get_frequency_display()})"

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)
            slug, n = base, 1
            while RecurringEvent.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"; n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def next_dates(self, from_date, count=20):
        """Return up to `count` upcoming occurrence dates (date objects) from from_date."""
        import calendar as _cal
        from datetime import timedelta, date

        results = []
        current = from_date

        def nth_weekday_of_month(year, month, weekday, n):
            """Return date of nth occurrence (1-based) of weekday in month. n=5 = last."""
            c = _cal.monthcalendar(year, month)
            days = [week[weekday] for week in c if week[weekday] != 0]
            if not days:
                return None
            idx = n - 1 if n < 5 else len(days) - 1
            return date(year, month, days[idx]) if idx < len(days) else None

        for _ in range(400):  # safety cap
            if len(results) >= count:
                break

            if self.frequency == self.FREQ_DAILY:
                candidate = current
                current += timedelta(days=self.interval)

            elif self.frequency == self.FREQ_WEEKLY:
                # Advance to next occurrence of day_of_week
                days_ahead = (self.day_of_week - current.weekday()) % 7
                if days_ahead == 0 and results:
                    days_ahead = 7 * self.interval
                candidate = current + timedelta(days=days_ahead)
                current = candidate + timedelta(days=7 * self.interval)

            elif self.frequency == self.FREQ_MONTHLY_DATE:
                import calendar as _cal2
                try:
                    candidate = current.replace(day=from_date.day)
                except ValueError:
                    # e.g. Feb 30 → skip
                    candidate = None
                # advance month
                month = current.month + self.interval
                year  = current.year + (month - 1) // 12
                month = ((month - 1) % 12) + 1
                current = current.replace(year=year, month=month, day=1)

            elif self.frequency == self.FREQ_MONTHLY_WEEKDAY:
                candidate = nth_weekday_of_month(
                    current.year, current.month, self.day_of_week, self.week_of_month
                )
                month = current.month + self.interval
                year  = current.year + (month - 1) // 12
                month = ((month - 1) % 12) + 1
                current = current.replace(year=year, month=month, day=1)

            elif self.frequency == self.FREQ_ANNUALLY:
                try:
                    candidate = from_date.replace(year=current.year)
                except ValueError:
                    candidate = None
                current = current.replace(year=current.year + self.interval)

            else:
                break

            if candidate and candidate >= from_date:
                results.append(candidate)

        return results


class Event(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    CATEGORY_CHOICES = [
        ('music',  'Music'),
        ('arts',   'Arts & Comedy'),
        ('bike',   'Bike'),
        ('fund',   'Fundraiser'),
        ('food',   'Food'),
        ('hybrid', 'Hybrid'),
    ]

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True, null=True)
    description = models.TextField()
    location = models.CharField(max_length=300)
    neighborhood = models.CharField(max_length=100, blank=True)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField(blank=True, null=True)
    photo = models.ImageField(upload_to='events/', blank=True, null=True)
    website = models.URLField(blank=True)
    submitted_by = models.CharField(max_length=100, blank=True)
    submitted_email = models.EmailField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    is_free = models.BooleanField(default=True)
    price_info = models.CharField(max_length=100, blank=True)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, blank=True)
    extra_links = models.JSONField(default=list, blank=True)
    submitted_user = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='events'
    )
    genres  = models.ManyToManyField('Genre',  blank=True, related_name='events')
    artists = models.ManyToManyField('Artist', blank=True, related_name='events',
                                     help_text='Performing artists / headliners')
    recurring_event = models.ForeignKey('RecurringEvent', null=True, blank=True,
                        on_delete=models.SET_NULL, related_name='instances')
    latitude = models.FloatField(blank=True, null=True)
    longitude = models.FloatField(blank=True, null=True)

    class Meta:
        ordering = ['start_date']

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(f"{self.title}-{self.start_date.strftime('%Y-%m-%d')}")
            slug = base
            counter = 1
            while Event.objects.filter(slug=slug).exists():
                slug = f"{base}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.title} — {self.start_date.strftime('%b %d %Y')}"

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('event_detail', kwargs={'slug': self.slug})


class EventPhoto(models.Model):
    PHOTO_TYPE_CHOICES = [
        ('promo', 'Promo / Flyer'),
        ('recap', 'Event Recap'),
    ]

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='photos')
    image = models.ImageField(upload_to='event_photos/%Y/%m/')
    caption = models.CharField(max_length=200, blank=True)
    photo_type = models.CharField(max_length=10, choices=PHOTO_TYPE_CHOICES, default='promo')
    submitted_by = models.CharField(max_length=100, blank=True)
    submitted_email = models.EmailField(blank=True)
    approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.photo_type} photo for {self.event.title}"