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
    genres = models.ManyToManyField('Genre', blank=True, related_name='events')
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