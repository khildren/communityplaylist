from django.db import models
from django.db.models import F
from django.utils.text import slugify
import re
import secrets


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
    SOURCE_ICAL         = 'ical'
    SOURCE_EVENTBRITE   = 'eventbrite'
    SOURCE_MUSICBRAINZ  = 'musicbrainz'
    SOURCE_SQUARESPACE  = 'squarespace'
    SOURCE_19HZ         = '19hz'
    SOURCE_EAEL         = 'eael'
    SOURCE_CHOICES = [
        (SOURCE_ICAL,         'iCal Feed'),
        (SOURCE_EVENTBRITE,   'Eventbrite API'),
        (SOURCE_MUSICBRAINZ,  'MusicBrainz API'),
        (SOURCE_SQUARESPACE,  'Squarespace Events JSON'),
        (SOURCE_19HZ,         '19hz.info PNW Listing'),
        (SOURCE_EAEL,         'EAEL WordPress Calendar (data-events scrape)'),
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
    promoter         = models.ForeignKey('PromoterProfile', null=True, blank=True,
                                         on_delete=models.SET_NULL, related_name='feeds',
                                         help_text='Promoter/crew this feed belongs to — events are auto-linked to their profile')
    last_synced     = models.DateTimeField(null=True, blank=True)
    last_error      = models.TextField(blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)
    notes           = models.TextField(blank=True, help_text='Internal notes about this source')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class SiteStats(models.Model):
    visit_count      = models.BigIntegerField(default=0)
    daily_count      = models.BigIntegerField(default=0)
    daily_date       = models.DateField(null=True, blank=True)
    tracking_started = models.DateField(null=True, blank=True,
                                        help_text='Date counting began — used to compute all-time avg daily')

    class Meta:
        verbose_name_plural = 'site stats'

    @classmethod
    def record_visit(cls, request):
        if not request.session.get('cp_counted'):
            from django.utils import timezone
            today = timezone.localdate()
            obj, created = cls.objects.get_or_create(pk=1)
            if created or obj.tracking_started is None:
                cls.objects.filter(pk=1).update(tracking_started=today)
            if obj.daily_date != today:
                cls.objects.filter(pk=1).update(
                    visit_count=F('visit_count') + 1,
                    daily_count=1,
                    daily_date=today,
                )
            else:
                cls.objects.filter(pk=1).update(
                    visit_count=F('visit_count') + 1,
                    daily_count=F('daily_count') + 1,
                )
            request.session['cp_counted'] = True

    @classmethod
    def get_count(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj.visit_count

    @classmethod
    def get_counts(cls):
        import math
        from django.utils import timezone
        obj, _ = cls.objects.get_or_create(pk=1)
        today = timezone.localdate()
        if obj.tracking_started:
            days = max(1, (today - obj.tracking_started).days + 1)
            avg_daily = math.ceil(obj.visit_count / days)
        else:
            avg_daily = obj.daily_count if obj.daily_date == today else 0
        return obj.visit_count, avg_daily


class Genre(models.Model):
    name = models.CharField(max_length=100, unique=True)
    mb_id = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Artist(models.Model):
    name   = models.CharField(max_length=200, unique=True)
    slug   = models.SlugField(max_length=220, unique=True, blank=True, null=True)
    mb_id  = models.CharField(max_length=100, blank=True, help_text='MusicBrainz artist ID')
    bio    = models.TextField(blank=True)
    website = models.URLField(blank=True)
    photo  = models.ImageField(upload_to='artists/', blank=True, null=True)

    # Social links
    instagram  = models.CharField(max_length=100, blank=True, help_text='Handle without @')
    soundcloud = models.CharField(max_length=100, blank=True, help_text='Username / profile slug')
    bandcamp   = models.URLField(blank=True, help_text='Full Bandcamp URL')
    mixcloud   = models.CharField(max_length=100, blank=True, help_text='Username / profile slug')
    youtube    = models.URLField(blank=True)
    spotify    = models.URLField(blank=True, help_text='Artist page URL')
    mastodon   = models.URLField(blank=True, help_text='Full profile URL e.g. https://pdx.social/@you')
    bluesky    = models.CharField(max_length=100, blank=True, help_text='Handle e.g. yourname.bsky.social')
    kofi       = models.CharField(max_length=100, blank=True, help_text='Ko-fi username e.g. yourname from ko-fi.com/yourname')
    tiktok     = models.CharField(max_length=100, blank=True, help_text='Handle without @')
    twitch     = models.CharField(max_length=100, blank=True, help_text='Channel name without @')
    beatport   = models.URLField(blank=True, help_text='Beatport artist page URL')
    discogs    = models.URLField(blank=True, help_text='Discogs artist page URL')
    house_mixes = models.CharField(max_length=100, blank=True, help_text='house-mixes.com username')
    HOUSE_MIXES_SORT_CHOICES = [
        ('newest',    'Newest first'),
        ('oldest',    'Oldest first'),
        ('downloads', 'Most downloaded'),
        ('plays',     'Most played'),
    ]
    house_mixes_sort = models.CharField(
        max_length=20, blank=True, default='newest',
        choices=HOUSE_MIXES_SORT_CHOICES,
        help_text='Sort order for house-mixes.com track list',
    )

    brand_color = models.CharField(
        max_length=7, blank=True, default='',
        help_text='Profile accent hex color e.g. #ff6b35 — leave blank for default orange',
    )

    # Music folder
    drive_folder_url = models.URLField(
        blank=True,
        help_text='Public Google Drive folder URL — live sets, DJ sessions, mixes',
    )

    admin_email = models.EmailField(
        blank=True,
        help_text='Internal contact email — used to send claim instructions. Not shown publicly.',
    )

    claimed_by = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='claimed_artists',
    )
    is_verified = models.BooleanField(default=False, help_text='Admin-verified artist')
    is_live     = models.BooleanField(default=False, help_text='Currently streaming live (updated by check_live_streams)')
    twitch_unresolvable = models.BooleanField(default=False, help_text='Twitch username returned 400 — needs review')
    link_broken      = models.BooleanField(default=False, help_text='Website URL returned 4xx/5xx (check_links cron)')
    link_checked_at  = models.DateTimeField(null=True, blank=True, help_text='Last time website URL was checked')
    youtube_channel_id = models.CharField(max_length=50, blank=True, help_text='Cached YouTube channel ID (UCxxx…)')
    view_count  = models.PositiveIntegerField(default=0)
    allow_comments = models.BooleanField(default=False, help_text='Allow public comments on profile page')

    # Auto-build / enrichment
    is_stub          = models.BooleanField(default=False, help_text='Auto-generated from events — not yet claimed')
    city             = models.CharField(max_length=100, blank=True, help_text='Derived from event venues or platform profile')
    latitude         = models.FloatField(null=True, blank=True, help_text='Geo center derived from event venue cluster')
    longitude        = models.FloatField(null=True, blank=True)
    home_neighborhood = models.CharField(max_length=100, blank=True, help_text='Most frequent event neighborhood')
    auto_bio         = models.TextField(blank=True, help_text='System-generated bio from event history — replaced by artist bio on claim')
    last_enriched_at = models.DateTimeField(null=True, blank=True, help_text='Last time enrichment was run for this artist')

    # Crew / alias linkage
    linked_promoter  = models.ForeignKey(
        'PromoterProfile', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='linked_artists',
        help_text='If this artist record is also a crew/collective, link their PromoterProfile here',
    )

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)
            slug, n = base, 1
            while Artist.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f'{base}-{n}'; n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('artist_profile', kwargs={'slug': self.slug})


class PromoterProfile(models.Model):
    """A promoter, sound system, crew, label, or collective with a public profile."""

    TYPE_CREW        = 'crew'
    TYPE_SOUND       = 'sound_system'
    TYPE_COLLECTIVE  = 'collective'
    TYPE_LABEL       = 'label'
    TYPE_RECORD_SWAP = 'record_swap'
    TYPE_CHOICES = [
        (TYPE_CREW,        'Crew'),
        (TYPE_SOUND,       'Sound System'),
        (TYPE_COLLECTIVE,  'Collective'),
        (TYPE_LABEL,       'Record Label'),
        (TYPE_RECORD_SWAP, 'Record Swap'),
    ]
    # Icon glyphs matched to each type (used in templates via a mapping filter)
    TYPE_ICONS = {
        TYPE_CREW:        '📣',
        TYPE_SOUND:       '🔊',
        TYPE_COLLECTIVE:  '🤝',
        TYPE_LABEL:       '💿',
        TYPE_RECORD_SWAP: '🎵',
    }

    name          = models.CharField(max_length=200, unique=True)
    slug          = models.SlugField(max_length=220, unique=True, blank=True)
    promoter_type = models.JSONField(
        default=list,
        help_text='One or more types — stored as a JSON list, e.g. ["crew", "record_swap"]',
    )
    bio    = models.TextField(blank=True)
    photo  = models.ImageField(upload_to='promoters/', blank=True, null=True)
    website = models.URLField(blank=True)

    # Social links (same set as Artist)
    instagram  = models.CharField(max_length=100, blank=True, help_text='Handle without @')
    soundcloud = models.CharField(max_length=100, blank=True, help_text='Username / profile slug')
    bandcamp   = models.URLField(blank=True)
    mixcloud   = models.CharField(max_length=100, blank=True, help_text='Username / profile slug')
    youtube    = models.URLField(blank=True)
    spotify    = models.URLField(blank=True)
    mastodon   = models.URLField(blank=True, help_text='Full profile URL')
    bluesky    = models.CharField(max_length=100, blank=True)
    kofi       = models.CharField(max_length=100, blank=True, help_text='Ko-fi username e.g. yourname from ko-fi.com/yourname')
    tiktok     = models.CharField(max_length=100, blank=True)
    twitch     = models.CharField(max_length=100, blank=True, help_text='Channel name without @')
    discord    = models.URLField(blank=True, help_text='Invite link')
    telegram   = models.CharField(max_length=100, blank=True, help_text='Username without @')

    brand_color = models.CharField(
        max_length=7, blank=True, default='',
        help_text='Profile accent hex color e.g. #ff6b35 — leave blank for default orange',
    )

    genres  = models.ManyToManyField('Genre', blank=True, related_name='promoters')
    members = models.ManyToManyField('Artist', blank=True, related_name='crews',
                                     help_text='Artists / DJs who are members of this crew')

    # Music folder
    drive_folder_url = models.URLField(
        blank=True,
        help_text='Public Google Drive folder URL — live sets, DJ sessions, mixes',
    )

    # Record shop
    shop_sheet_url = models.URLField(
        blank=True,
        help_text='Public Google Sheets URL — your record inventory (Artist, Title, Label, Year, Format, Condition, Price SOL, Notes)',
    )
    sol_wallet = models.CharField(
        max_length=120, blank=True,
        help_text='Solana wallet address — payments go here (e.g. Phantom public key)',
    )
    shop_pay_in_person = models.BooleanField(
        default=False,
        help_text='Accept in-person payment at events / pickup',
    )
    shop_open_to_trade = models.BooleanField(
        default=False,
        help_text='Open to record trades / partial trades',
    )

    admin_email = models.EmailField(
        blank=True,
        help_text='Internal contact email — used to send claim instructions. Not shown publicly.',
    )

    claimed_by = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='claimed_promoters',
    )
    is_verified = models.BooleanField(default=False)
    is_public   = models.BooleanField(default=True)
    is_live     = models.BooleanField(default=False, help_text='Currently streaming live (updated by check_live_streams)')
    twitch_unresolvable = models.BooleanField(default=False, help_text='Twitch username returned 400 — needs review')
    link_broken      = models.BooleanField(default=False, help_text='Website URL returned 4xx/5xx (check_links cron)')
    link_checked_at  = models.DateTimeField(null=True, blank=True, help_text='Last time website URL was checked')
    youtube_channel_id = models.CharField(max_length=50, blank=True, help_text='Cached YouTube channel ID (UCxxx…)')
    created_at  = models.DateTimeField(auto_now_add=True)
    view_count  = models.PositiveIntegerField(default=0)
    allow_comments = models.BooleanField(default=False, help_text='Allow public comments on profile page')
    name_variants = models.TextField(
        blank=True,
        help_text='Pipe-separated name aliases that should resolve to this profile '
                  '(e.g. "Subduction Audio & Friends|Subduction Audio Crew"). '
                  'Used by the event parser to consolidate mismatched listings.',
    )

    class Meta:
        ordering = ['name']
        verbose_name = 'Promoter / Crew'
        verbose_name_plural = 'Promoters / Crews'

    def __str__(self):
        return self.name

    @property
    def types(self):
        """Always return promoter_type as a list, even if DB contains a legacy string."""
        val = self.promoter_type
        if isinstance(val, list):
            return val
        if isinstance(val, str) and val:
            return [val]
        return [self.TYPE_CREW]

    def has_type(self, type_key):
        return type_key in self.types

    def get_types_display(self):
        label_map = dict(self.TYPE_CHOICES)
        return ' · '.join(label_map.get(t, t) for t in self.types)

    def get_type_icons(self):
        return ' '.join(self.TYPE_ICONS.get(t, '📣') for t in self.types)

    @property
    def type_badges(self):
        """List of (icon, label) tuples for each type — easy to iterate in templates."""
        label_map = dict(self.TYPE_CHOICES)
        return [(self.TYPE_ICONS.get(t, '📣'), label_map.get(t, t)) for t in self.types]

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)
            slug, n = base, 1
            while PromoterProfile.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f'{base}-{n}'; n += 1
            self.slug = slug
        # Normalise to list before saving
        if isinstance(self.promoter_type, str):
            self.promoter_type = [self.promoter_type] if self.promoter_type else [self.TYPE_CREW]
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('promoter_detail', kwargs={'slug': self.slug})


class CommunitySpace(models.Model):
    """A community garden, third space, makerspace, or free public gathering place."""

    TYPE_GARDEN      = 'garden'
    TYPE_THIRD_SPACE = 'third_space'
    TYPE_MAKERSPACE  = 'makerspace'
    TYPE_LIBRARY     = 'library'
    TYPE_PARK        = 'park'
    TYPE_CHOICES = [
        (TYPE_GARDEN,      'Community Garden'),
        (TYPE_THIRD_SPACE, 'Third Space'),
        (TYPE_MAKERSPACE,  'Makerspace / Hackerspace'),
        (TYPE_LIBRARY,     'Free Library'),
        (TYPE_PARK,        'Park / Outdoor Space'),
    ]

    name       = models.CharField(max_length=200)
    slug       = models.SlugField(max_length=220, unique=True, blank=True)
    space_type = models.CharField(max_length=30, choices=TYPE_CHOICES, default=TYPE_GARDEN)
    bio        = models.TextField(blank=True)
    photo      = models.ImageField(upload_to='community_spaces/', blank=True, null=True)
    brand_color = models.CharField(
        max_length=7, blank=True, default='',
        help_text='Profile accent hex color e.g. #4caf50 — leave blank for default green',
    )

    # Physical location
    address      = models.CharField(max_length=300, blank=True)
    neighborhood = models.CharField(max_length=100, blank=True)
    latitude     = models.FloatField(null=True, blank=True)
    longitude    = models.FloatField(null=True, blank=True)

    # Contact (public-facing)
    contact_email = models.EmailField(blank=True, help_text='Displayed on profile — use a public contact address')
    website       = models.URLField(blank=True)

    # Social — fedi/indie first
    instagram = models.CharField(max_length=100, blank=True, help_text='Handle without @')
    bluesky   = models.CharField(max_length=100, blank=True, help_text='Handle e.g. you.bsky.social')
    mastodon  = models.URLField(blank=True, help_text='Full profile URL e.g. https://pdx.social/@you')
    tiktok    = models.CharField(max_length=100, blank=True, help_text='Handle without @')

    # Resources
    drive_folder_url = models.URLField(
        blank=True, help_text='Public Google Drive folder — shows as "Resource Library" button',
    )
    show_audio = models.BooleanField(
        default=False,
        help_text='Display audio files from the Drive folder as an inline player on the profile',
    )
    show_docs = models.BooleanField(
        default=False,
        help_text='Display PDFs / Google Docs / zines from the Drive folder as a document library on the profile',
    )

    # Funding — fedi/crypto only (no PayPal/Stripe)
    sol_wallet   = models.CharField(
        max_length=120, blank=True,
        help_text='Solana wallet address (Phantom) — shows a ♥ Donate button',
    )
    donation_url = models.URLField(
        blank=True,
        help_text='Ko-fi, Open Collective, or Helium link — shown alongside SOL wallet',
    )

    # Custom link buttons (Linktree-style)
    custom_links = models.JSONField(
        default=list,
        blank=True,
        help_text='Up to 8 custom buttons: [{"label": "Code of Conduct", "url": "https://...", "thumbnail_url": ""}]',
    )

    claimed_by  = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='claimed_spaces',
    )
    is_verified = models.BooleanField(default=False)
    is_public   = models.BooleanField(default=True)
    view_count  = models.PositiveIntegerField(default=0)
    allow_comments = models.BooleanField(default=False, help_text='Allow public comments on profile page')
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Community Space'
        verbose_name_plural = 'Community Spaces'

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)
            slug, n = base, 1
            while CommunitySpace.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f'{base}-{n}'; n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('community_space_profile', kwargs={'slug': self.slug})


class CommunityAsk(models.Model):
    """A specific need or request posted by a Venue or CommunitySpace."""

    TYPE_FUND      = 'fund'
    TYPE_ITEM      = 'item'
    TYPE_VOLUNTEER = 'volunteer'
    TYPE_SKILL     = 'skill'
    TYPE_CHOICES = [
        (TYPE_FUND,      'Funding / Donation'),
        (TYPE_ITEM,      'Item / Equipment'),
        (TYPE_VOLUNTEER, 'Volunteer Time'),
        (TYPE_SKILL,     'Skill / Service'),
    ]

    STATUS_OPEN        = 'open'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_FULFILLED   = 'fulfilled'
    STATUS_CHOICES = [
        (STATUS_OPEN,        'Open'),
        (STATUS_IN_PROGRESS, 'In Progress'),
        (STATUS_FULFILLED,   'Fulfilled — thank you!'),
    ]

    community_space = models.ForeignKey(
        'CommunitySpace', null=True, blank=True, on_delete=models.CASCADE,
        related_name='asks',
    )
    venue = models.ForeignKey(
        'Venue', null=True, blank=True, on_delete=models.CASCADE,
        related_name='asks',
    )

    title         = models.CharField(max_length=200)
    description   = models.TextField(blank=True)
    ask_type      = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_ITEM)
    target_amount = models.DecimalField(
        max_digits=8, decimal_places=0, null=True, blank=True,
        help_text='Funding goal in dollars (optional)',
    )
    donation_url  = models.URLField(
        blank=True,
        help_text='Specific donate link for this ask — overrides profile donation URL',
    )

    # Product wishlist fields (item asks)
    product_url       = models.URLField(blank=True, help_text='Link to specific item on Amazon or elsewhere')
    product_image_url = models.URLField(blank=True, help_text='Product thumbnail URL (auto-fetchable from page)')
    product_price     = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text='Approximate cost in dollars',
    )

    # Board integration — linked ISO ("In Search Of") offering
    board_offering = models.OneToOneField(
        'board.Offering', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='community_ask',
        help_text='Living Buy Nothing ISO post linked to this ask',
    )

    status     = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sort_order', 'created_at']
        verbose_name = 'Community Ask'
        verbose_name_plural = 'Community Asks'

    def __str__(self):
        owner = self.community_space or self.venue or '?'
        return f'{self.get_ask_type_display()} — {self.title} [{owner}]'

    @property
    def board_url(self):
        if self.board_offering_id:
            return self.board_offering.get_absolute_url()
        return ''


class RecordListing(models.Model):
    """A single record/item in a promoter's SOL shop, synced from a Google Sheet."""
    CONDITION_CHOICES = [
        ('M',   'Mint (M)'),
        ('NM',  'Near Mint (NM)'),
        ('VG+', 'Very Good Plus (VG+)'),
        ('VG',  'Very Good (VG)'),
        ('G+',  'Good Plus (G+)'),
        ('G',   'Good (G)'),
        ('F',   'Fair (F)'),
        ('P',   'Poor (P)'),
    ]

    promoter    = models.ForeignKey(PromoterProfile, on_delete=models.CASCADE,
                                    related_name='record_listings')
    row_index   = models.PositiveIntegerField(default=0, help_text='Row position in sheet (for ordering/dedup)')
    artist      = models.CharField(max_length=200)
    title       = models.CharField(max_length=200)
    label       = models.CharField(max_length=200, blank=True)
    year        = models.CharField(max_length=10, blank=True)
    format      = models.CharField(max_length=50, blank=True, help_text='e.g. Vinyl, LP, 12", CD')
    condition   = models.CharField(max_length=4, blank=True, choices=CONDITION_CHOICES)
    price_sol     = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    price_display = models.CharField(max_length=50, blank=True,
                                     help_text='Raw price from sheet e.g. "$ 30" or "0.5 SOL"')
    notes         = models.TextField(blank=True)
    cover_url   = models.URLField(blank=True, help_text='Pulled from Discogs')
    preview_url = models.URLField(blank=True, help_text='YouTube video URL from Discogs — for in-card preview player')
    discogs_id  = models.CharField(max_length=30, blank=True)
    genres      = models.CharField(max_length=200, blank=True, help_text='Discogs genres, comma-separated')
    styles      = models.CharField(max_length=200, blank=True, help_text='Discogs styles, comma-separated')
    is_available = models.BooleanField(default=True)
    synced_at   = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['row_index']

    def __str__(self):
        return f'{self.artist} — {self.title} ({self.promoter.name})'


class RecordReservation(models.Model):
    STATUS_PENDING   = 'pending'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_SOLD      = 'sold'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_PENDING,   'Pending'),
        (STATUS_CONFIRMED, 'Confirmed'),
        (STATUS_SOLD,      'Sold'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    listing      = models.ForeignKey(RecordListing, on_delete=models.CASCADE,
                                     related_name='reservations')
    buyer        = models.ForeignKey('auth.User', null=True, blank=True,
                                     on_delete=models.SET_NULL,
                                     related_name='record_reservations')
    buyer_name   = models.CharField(max_length=120)
    buyer_email  = models.EmailField(blank=True)
    buyer_contact = models.CharField(max_length=200, blank=True,
                                     help_text='Discord handle, Telegram, phone, etc.')
    message      = models.TextField(blank=True)
    status       = models.CharField(max_length=12, choices=STATUS_CHOICES,
                                    default=STATUS_PENDING)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.buyer_name} → {self.listing} [{self.status}]'


class PlaylistTrack(models.Model):
    """A single audio track sourced from a Google Drive folder."""
    # Source — exactly one of these will be set
    artist   = models.ForeignKey(Artist, null=True, blank=True,
                                 on_delete=models.CASCADE, related_name='tracks')
    promoter = models.ForeignKey('PromoterProfile', null=True, blank=True,
                                 on_delete=models.CASCADE, related_name='tracks')
    venue    = models.ForeignKey('Venue', null=True, blank=True,
                                 on_delete=models.CASCADE, related_name='tracks')

    drive_file_id = models.CharField(max_length=200, unique=True)
    title         = models.CharField(max_length=300)
    artist_name   = models.CharField(max_length=200, blank=True, help_text='Parsed from filename or metadata')
    genre         = models.ForeignKey('Genre', null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name='tracks')
    genre_raw     = models.CharField(max_length=100, blank=True, help_text='Raw genre string from filename')
    recorded_at   = models.CharField(max_length=200, blank=True, help_text='Venue or event name')
    recorded_date = models.DateField(null=True, blank=True)
    duration_secs = models.PositiveIntegerField(null=True, blank=True)
    stream_url    = models.URLField(max_length=500, blank=True, help_text='Cached direct stream URL')
    mime_type     = models.CharField(max_length=50, blank=True)
    position      = models.PositiveIntegerField(default=0, help_text='Sort order within folder')
    last_synced   = models.DateTimeField(auto_now=True)
    created_at    = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        ordering = ['position', 'title']

    def __str__(self):
        return f'{self.title} — {self.artist_name or "Unknown"}'

    @property
    def stream_url_direct(self):
        """Construct a streamable URL from the Drive file ID."""
        return f'https://www.googleapis.com/drive/v3/files/{self.drive_file_id}?alt=media'

    @property
    def duration_display(self):
        """Human-readable duration, e.g. '1h 23m' or '45:32'."""
        if not self.duration_secs:
            return ''
        s = self.duration_secs
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        if h:
            return f'{h}h {m:02d}m'
        return f'{m}:{sec:02d}'

    @property
    def source_label(self):
        if self.artist:
            return self.artist.name
        if self.promoter:
            return self.promoter.name
        if self.venue:
            return self.venue.name
        return ''


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
        ('pending',   'Pending Review'),
        ('approved',  'Approved'),
        ('rejected',  'Rejected'),
        ('cancelled', 'Cancelled'),
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
    genres     = models.ManyToManyField('Genre',  blank=True, related_name='events')
    artists    = models.ManyToManyField('Artist', blank=True, related_name='events',
                                        help_text='Performing artists / headliners')
    promoters  = models.ManyToManyField('PromoterProfile', blank=True, related_name='events',
                                        help_text='Crews / promoters for this event')
    recurring_event = models.ForeignKey('RecurringEvent', null=True, blank=True,
                        on_delete=models.SET_NULL, related_name='instances')
    latitude = models.FloatField(blank=True, null=True)
    longitude = models.FloatField(blank=True, null=True)
    view_count = models.PositiveIntegerField(default=0)
    flyer_url      = models.URLField(blank=True, help_text='Instagram post URL or direct flyer image URL — used for AI enrichment')
    flyer_scanned  = models.BooleanField(default=False, help_text='Set once moondream has scanned this flyer')

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


class Venue(models.Model):
    """Public profile for a PDX venue space."""
    name         = models.CharField(max_length=200)
    slug         = models.SlugField(max_length=220, unique=True, blank=True)
    description  = models.TextField(blank=True)
    address      = models.CharField(max_length=300, help_text='Physical address — used to match events automatically')
    neighborhood = models.CharField(max_length=100, blank=True)
    latitude     = models.FloatField(null=True, blank=True)
    longitude    = models.FloatField(null=True, blank=True)
    website      = models.URLField(blank=True)
    logo         = models.ImageField(upload_to='venues/', blank=True, null=True)
    instagram    = models.CharField(max_length=100, blank=True, help_text='Handle without @')
    bandcamp     = models.URLField(blank=True)
    youtube      = models.URLField(blank=True)
    twitter      = models.CharField(max_length=100, blank=True, help_text='Handle without @')
    mastodon     = models.URLField(blank=True, help_text='Full profile URL e.g. https://pdx.social/@yourhandle')
    discord      = models.URLField(blank=True, help_text='Invite link')
    medium       = models.CharField(max_length=100, blank=True, help_text='Username without @')
    bluesky      = models.CharField(max_length=100, blank=True, help_text='Handle without @ e.g. yourname.bsky.social')
    linkedin     = models.CharField(max_length=100, blank=True, help_text='Company page slug')
    tiktok       = models.CharField(max_length=100, blank=True, help_text='Handle without @')
    twitch       = models.CharField(max_length=100, blank=True, help_text='Channel name without @')
    threads      = models.CharField(max_length=100, blank=True, help_text='Handle without @')
    soundcloud   = models.CharField(max_length=100, blank=True, help_text='Username / profile slug')
    mixcloud     = models.CharField(max_length=100, blank=True, help_text='Username / profile slug')
    brand_color  = models.CharField(
        max_length=7, blank=True, default='',
        help_text='Profile accent hex color e.g. #ff6b35 — leave blank for default orange',
    )
    venue_feed   = models.OneToOneField(
        'VenueFeed', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='venue_profile',
        help_text='Link to the iCal feed we import events from (optional)'
    )
    claimed_by   = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='claimed_venues'
    )
    verified     = models.BooleanField(default=False, help_text='Admin-verified venue')
    active       = models.BooleanField(default=True, help_text='Uncheck to mark venue as permanently closed')
    closed_date  = models.DateField(null=True, blank=True, help_text='Date venue closed (for display)')
    link_broken      = models.BooleanField(default=False, help_text='Website URL returned 4xx/5xx (check_links cron)')
    link_checked_at  = models.DateTimeField(null=True, blank=True, help_text='Last time website URL was checked')
    is_live      = models.BooleanField(default=False, help_text='Currently streaming live (updated by check_live_streams)')
    youtube_channel_id = models.CharField(max_length=50, blank=True, help_text='Cached YouTube channel ID (UCxxx…)')
    view_count   = models.PositiveIntegerField(default=0)
    allow_comments = models.BooleanField(default=False, help_text='Allow public comments on venue page')
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)
            slug, n = base, 1
            while Venue.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"; n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_events(self):
        """Approved events at this venue, matched by name/address substring."""
        import unicodedata
        from django.db.models import Q

        def _ascii(s):
            return unicodedata.normalize('NFD', s).encode('ascii', 'ignore').decode()

        q = Q(location__icontains=self.name) | Q(location__icontains=_ascii(self.name))
        if self.address and len(self.address) > 8:
            addr40 = self.address[:40]
            q |= Q(location__icontains=addr40) | Q(location__icontains=_ascii(addr40))
        return Event.objects.filter(status='approved').filter(q).order_by('start_date')

    @classmethod
    def for_location(cls, location):
        """Return the best matching active Venue for an event location string, or None."""
        import unicodedata

        def _fold(s):
            """Lowercase + strip accents for accent-insensitive comparison."""
            return unicodedata.normalize('NFD', s.lower()).encode('ascii', 'ignore').decode()

        if not location or location.startswith(('http://', 'https://', 'www.')):
            return None
        loc_f = _fold(location)
        for v in cls.objects.filter(active=True).only('id', 'name', 'address', 'slug'):
            name_f = _fold(v.name.strip())
            addr_f = _fold(v.address.strip())
            if name_f and len(name_f) > 4 and name_f in loc_f:
                return v
            if addr_f and len(addr_f) > 8 and addr_f[:40] in loc_f:
                return v
        return None


class Neighborhood(models.Model):
    """A Portland-area neighborhood with its own page, board, and history blurb."""
    name        = models.CharField(max_length=100, unique=True)
    slug        = models.SlugField(max_length=120, unique=True, blank=True)
    description = models.TextField(
        blank=True,
        help_text='Short history / character blurb — shown on neighborhood page. '
                  'Sourced from Wikipedia or written by hand.',
    )
    wiki_url    = models.URLField(blank=True, help_text='Wikipedia article URL for attribution')
    aliases     = models.TextField(
        blank=True,
        help_text='Pipe-separated list of alternative names used in event.neighborhood (e.g. "Eliot|Boise"). '
                  'Used when the display name differs from how events are tagged.',
    )
    latitude    = models.FloatField(null=True, blank=True, help_text='Center-point for distance queries')
    longitude   = models.FloatField(null=True, blank=True)
    active      = models.BooleanField(default=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    view_count  = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)
            slug, n = base, 1
            while Neighborhood.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f'{base}-{n}'; n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def event_q(self):
        """Return a Q object matching any of this neighborhood's name/aliases."""
        from django.db.models import Q
        q = Q(neighborhood__icontains=self.name)
        for alias in self.aliases.split('|'):
            alias = alias.strip()
            if alias:
                q |= Q(neighborhood__icontains=alias)
        return q

    def upcoming_events(self):
        from django.utils import timezone
        return Event.objects.filter(
            status='approved',
            start_date__gte=timezone.now(),
        ).filter(self.event_q()).order_by('start_date')


class UserProfile(models.Model):
    """One-to-one profile extending Django's built-in User."""
    user         = models.OneToOneField('auth.User', on_delete=models.CASCADE, related_name='profile')
    handle       = models.CharField(
        max_length=50, unique=True,
        help_text='Public @handle — lowercase letters, numbers, underscores only.',
    )
    pronouns     = models.CharField(max_length=40, blank=True)
    bio          = models.TextField(max_length=500, blank=True)
    links        = models.JSONField(
        default=list, blank=True,
        help_text='List of {"label": "...", "url": "..."} dicts.',
    )
    is_public    = models.BooleanField(default=True, help_text='Show public profile page')
    avatar       = models.ImageField(upload_to='avatars/', null=True, blank=True)
    email_verified  = models.BooleanField(default=False)
    verify_token    = models.CharField(max_length=64, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    # Profile type flags — each unlocks the matching dashboard section
    wants_artist   = models.BooleanField(default=False, help_text='User has or wants an artist profile')
    wants_promoter = models.BooleanField(default=False, help_text='User has or wants a crew/promoter profile')
    wants_venue    = models.BooleanField(default=False, help_text='User has or wants a venue profile')

    # Contact / web3
    messenger_telegram = models.CharField(
        max_length=100, blank=True,
        help_text='Telegram handle without @ — links to t.me/handle',
    )
    messenger_discord = models.CharField(
        max_length=30, blank=True,
        help_text='Discord user ID (numeric) — links to discord.com/users/ID. Find it: Settings → Advanced → Developer Mode, then right-click your name.',
    )
    messenger_signal = models.CharField(
        max_length=100, blank=True,
        help_text='Signal username (without +) — links to signal.me',
    )
    sol_wallet = models.CharField(
        max_length=120, blank=True,
        help_text='Solana wallet address (Phantom public key, etc.)',
    )

    # Music service usernames
    lastfm_username        = models.CharField(max_length=100, blank=True, help_text='Last.fm username')
    listenbrainz_username  = models.CharField(max_length=100, blank=True, help_text='ListenBrainz username')
    discogs_username       = models.CharField(max_length=100, blank=True, help_text='Discogs username')

    # Public profile privacy controls
    show_embeds          = models.BooleanField(default=True,  help_text='Show music embeds on public profile')
    show_following       = models.BooleanField(default=False, help_text='Show following list on public profile')
    show_saved_tracks    = models.BooleanField(default=False, help_text='Show saved tracks on public profile')
    show_rss_feed        = models.BooleanField(default=False, help_text='Show RSS feed link on public profile')
    show_upcoming_events = models.BooleanField(default=True,  help_text='Show upcoming events on public profile')

    # Onboarding state
    onboarded = models.BooleanField(
        default=False,
        help_text='Completed post-signup profile type picker',
    )

    class Meta:
        verbose_name = 'User Profile'

    def __str__(self):
        return f'@{self.handle}'

    @staticmethod
    def generate_token():
        return secrets.token_urlsafe(32)

    @staticmethod
    def handle_from_email(email):
        """Derive a safe default handle from an email address."""
        base = re.sub(r'[^a-z0-9_]', '_', email.split('@')[0].lower())[:40].strip('_') or 'user'
        handle, n = base, 1
        while UserProfile.objects.filter(handle=handle).exists():
            handle = f'{base}_{n}'; n += 1
        return handle

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('public_profile', kwargs={'handle': self.handle})


class EditSuggestion(models.Model):
    """A logged-in user's suggested edit to an event, venue, artist, or neighborhood."""
    TYPE_EVENT        = 'event'
    TYPE_VENUE        = 'venue'
    TYPE_ARTIST       = 'artist'
    TYPE_NEIGHBORHOOD = 'neighborhood'
    TYPE_CHOICES = [
        (TYPE_EVENT,        'Event'),
        (TYPE_VENUE,        'Venue'),
        (TYPE_ARTIST,       'Artist'),
        (TYPE_NEIGHBORHOOD, 'Neighborhood'),
    ]
    STATUS_PENDING  = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_PENDING,  'Pending'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
    ]
    # Editable fields per target type
    FIELDS = {
        TYPE_EVENT:        [('description','Description'), ('location','Location'), ('price_info','Price info'), ('website','Website / tickets URL')],
        TYPE_VENUE:        [('description','Description'), ('address','Address'), ('website','Website')],
        TYPE_ARTIST:       [('bio','Bio'), ('website','Website')],
        TYPE_NEIGHBORHOOD: [('description','Description'), ('wiki_url','Wikipedia URL')],
    }

    user            = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='edit_suggestions')
    target_type     = models.CharField(max_length=20, choices=TYPE_CHOICES)
    target_id       = models.PositiveIntegerField()
    field_name      = models.CharField(max_length=50)
    current_value   = models.TextField(blank=True)
    suggested_value = models.TextField()
    note            = models.TextField(blank=True, help_text='Why this edit is needed (optional)')
    status          = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    reviewed_by     = models.ForeignKey(
        'auth.User', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='reviewed_suggestions',
    )
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Edit Suggestion'

    def __str__(self):
        return f'{self.target_type}:{self.target_id} · {self.field_name} ({self.status})'

    def get_target(self):
        if self.target_type == self.TYPE_EVENT:
            return Event.objects.filter(pk=self.target_id).first()
        if self.target_type == self.TYPE_VENUE:
            return Venue.objects.filter(pk=self.target_id).first()
        if self.target_type == self.TYPE_ARTIST:
            return Artist.objects.filter(pk=self.target_id).first()
        if self.target_type == self.TYPE_NEIGHBORHOOD:
            return Neighborhood.objects.filter(pk=self.target_id).first()
        return None

    def apply(self):
        """Write suggested_value to the target object's field and save."""
        target = self.get_target()
        if target and hasattr(target, self.field_name):
            setattr(target, self.field_name, self.suggested_value)
            target.save(update_fields=[self.field_name])
            return True
        return False


class Follow(models.Model):
    """A user following an artist, venue, neighborhood, or promoter."""
    TYPE_ARTIST       = 'artist'
    TYPE_VENUE        = 'venue'
    TYPE_NEIGHBORHOOD = 'neighborhood'
    TYPE_PROMOTER     = 'promoter'
    TYPE_SPACE        = 'space'
    TYPE_CHOICES = [
        (TYPE_ARTIST,       'Artist'),
        (TYPE_VENUE,        'Venue'),
        (TYPE_NEIGHBORHOOD, 'Neighborhood'),
        (TYPE_PROMOTER,     'Promoter'),
        (TYPE_SPACE,        'Community Space'),
    ]
    user        = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='follows')
    target_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    target_id   = models.PositiveIntegerField()
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('user', 'target_type', 'target_id')]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} → {self.target_type}:{self.target_id}'

    def get_target(self):
        """Return the actual object being followed."""
        if self.target_type == self.TYPE_ARTIST:
            return Artist.objects.filter(pk=self.target_id).first()
        if self.target_type == self.TYPE_VENUE:
            return Venue.objects.filter(pk=self.target_id).first()
        if self.target_type == self.TYPE_NEIGHBORHOOD:
            return Neighborhood.objects.filter(pk=self.target_id).first()
        if self.target_type == self.TYPE_PROMOTER:
            return PromoterProfile.objects.filter(pk=self.target_id).first()
        if self.target_type == self.TYPE_SPACE:
            return CommunitySpace.objects.filter(pk=self.target_id).first()
        return None


class SavedTrack(models.Model):
    """A user's saved (bookmarked) track."""
    user       = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='saved_tracks')
    track      = models.ForeignKey(PlaylistTrack, on_delete=models.CASCADE, related_name='saved_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('user', 'track')]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} ♥ {self.track}'


class VideoTrack(models.Model):
    """A video (YouTube or Twitch) harvested from a connected artist/venue/promoter channel."""

    SOURCE_YOUTUBE     = 'youtube'
    SOURCE_TWITCH_VOD  = 'twitch_vod'
    SOURCE_TWITCH_LIVE = 'twitch_live'
    SOURCE_CHOICES = [
        (SOURCE_YOUTUBE,     'YouTube'),
        (SOURCE_TWITCH_VOD,  'Twitch VOD'),
        (SOURCE_TWITCH_LIVE, 'Twitch Live'),
    ]

    # Content source type
    source_type = models.CharField(max_length=20, default=SOURCE_YOUTUBE,
                                   choices=SOURCE_CHOICES, db_index=True)

    # Profile source — exactly one of these will be set
    artist   = models.ForeignKey('Artist', null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name='videos')
    promoter = models.ForeignKey('PromoterProfile', null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name='videos')
    venue    = models.ForeignKey('Venue', null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name='videos')

    # YouTube-specific
    youtube_video_id   = models.CharField(max_length=60, unique=True,
                                          help_text='YouTube video ID, or synthetic twitch_live_{user} / twitch_vod_{id}')
    youtube_channel_id = models.CharField(max_length=40, blank=True, db_index=True)

    # Twitch-specific
    twitch_username = models.CharField(max_length=100, blank=True,
                                       help_text='Twitch channel name (for live streams and VODs)')
    twitch_video_id = models.CharField(max_length=30, blank=True,
                                       help_text='Twitch VOD ID (numeric)')

    # Shared metadata
    channel_title      = models.CharField(max_length=200, blank=True)
    title              = models.CharField(max_length=300)
    artist_name_display = models.CharField(max_length=200, blank=True,
                                           help_text='Denormalized display name')
    description        = models.TextField(blank=True)
    thumbnail_url      = models.URLField(max_length=500, blank=True)
    published_at       = models.DateTimeField(null=True, blank=True)
    duration_secs      = models.PositiveIntegerField(null=True, blank=True)

    # Live stream status (refreshed by harvest_twitch)
    is_live         = models.BooleanField(default=False, db_index=True)
    live_checked_at = models.DateTimeField(null=True, blank=True)
    live_viewer_count = models.PositiveIntegerField(null=True, blank=True)

    play_count  = models.PositiveIntegerField(default=0)
    is_active   = models.BooleanField(default=True, help_text='Uncheck to hide from MTV channel')
    created_at  = models.DateTimeField(auto_now_add=True)
    last_synced = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_live', '-published_at']

    def __str__(self):
        live = ' 🔴 LIVE' if self.is_live else ''
        return f'{self.artist_name_display or self.channel_title} — {self.title}{live}'

    @property
    def embed_url(self):
        if self.source_type == self.SOURCE_YOUTUBE:
            return (f'https://www.youtube.com/embed/{self.youtube_video_id}'
                    f'?enablejsapi=1&autoplay=1&rel=0&modestbranding=1&playsinline=1')
        if self.source_type == self.SOURCE_TWITCH_LIVE:
            return (f'https://player.twitch.tv/?channel={self.twitch_username}'
                    f'&parent=communityplaylist.com&autoplay=true')
        if self.source_type == self.SOURCE_TWITCH_VOD:
            return (f'https://player.twitch.tv/?video={self.twitch_video_id}'
                    f'&parent=communityplaylist.com&autoplay=true')
        return ''


class Shelter(models.Model):
    """A PDX-area shelter, resource center, or emergency service."""

    TYPE_EMERGENCY    = 'emergency'
    TYPE_WARMING      = 'warming'
    TYPE_COOLING      = 'cooling'
    TYPE_OVERNIGHT    = 'overnight'
    TYPE_DAY          = 'day'
    TYPE_HYGIENE      = 'hygiene'
    TYPE_TINY_HOME    = 'tiny_home'
    TYPE_TRANSITIONAL = 'transitional'
    TYPE_YOUTH        = 'youth'
    TYPE_FAMILY       = 'family'
    TYPE_WOMENS       = 'womens'
    TYPE_VETERAN      = 'veteran'
    TYPE_SOBERING     = 'sobering'
    TYPE_HOTLINE      = 'hotline'

    TYPE_CHOICES = [
        (TYPE_EMERGENCY,    'Emergency Shelter'),
        (TYPE_WARMING,      'Warming Center'),
        (TYPE_COOLING,      'Cooling Center'),
        (TYPE_OVERNIGHT,    'Overnight Shelter'),
        (TYPE_DAY,          'Day Shelter / Drop-in'),
        (TYPE_HYGIENE,      'Hygiene Services'),
        (TYPE_TINY_HOME,    'Tiny Home Village'),
        (TYPE_TRANSITIONAL, 'Transitional Housing'),
        (TYPE_YOUTH,        'Youth Shelter'),
        (TYPE_FAMILY,       'Family Shelter'),
        (TYPE_WOMENS,       "Women's Shelter"),
        (TYPE_VETERAN,      'Veterans Services'),
        (TYPE_SOBERING,     'Sobering / Detox Center'),
        (TYPE_HOTLINE,      'Hotline / Phone Resource'),
    ]

    ACCEPTS_ALL      = 'all'
    ACCEPTS_WOMEN    = 'women'
    ACCEPTS_MEN      = 'men'
    ACCEPTS_YOUTH    = 'youth'
    ACCEPTS_FAMILIES = 'families'
    ACCEPTS_LGBTQ    = 'lgbtq'
    ACCEPTS_VETERAN  = 'veteran'
    ACCEPTS_CHOICES = [
        (ACCEPTS_ALL,      'Everyone'),
        (ACCEPTS_WOMEN,    'Women / Non-binary'),
        (ACCEPTS_MEN,      'Men'),
        (ACCEPTS_YOUTH,    'Youth (under 25)'),
        (ACCEPTS_FAMILIES, 'Families with children'),
        (ACCEPTS_LGBTQ,    'LGBTQ+ affirming'),
        (ACCEPTS_VETERAN,  'Veterans'),
    ]

    name          = models.CharField(max_length=200)
    slug          = models.SlugField(max_length=220, unique=True, blank=True)
    shelter_type  = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_EMERGENCY)
    accepts       = models.CharField(max_length=20, choices=ACCEPTS_CHOICES, default=ACCEPTS_ALL,
                                     help_text='Primary population served')
    pets_ok       = models.BooleanField(default=False, help_text='Pets accepted')
    address       = models.CharField(max_length=300, blank=True)
    neighborhood  = models.CharField(max_length=100, blank=True)
    latitude      = models.FloatField(null=True, blank=True)
    longitude     = models.FloatField(null=True, blank=True)
    phone         = models.CharField(max_length=30, blank=True)
    website       = models.URLField(blank=True)
    hours         = models.CharField(max_length=300, blank=True,
                                     help_text='e.g. "Mon–Fri 7am–9pm" or "24/7"')
    capacity      = models.PositiveIntegerField(null=True, blank=True,
                                                help_text='Bed/mat capacity if known')
    notes         = models.TextField(blank=True,
                                     help_text='Intake requirements, IDs needed, languages, etc.')
    # Weather flags — when true this shelter is promoted during those alert conditions
    available_hot  = models.BooleanField(default=False,
                                         help_text='Cooling center — promote on hot-weather days (>90°F)')
    available_cold = models.BooleanField(default=True,
                                         help_text='Warming center — promote on cold days (<35°F)')
    available_smoke = models.BooleanField(default=False,
                                          help_text='Indoor/filtered air — promote on high-particulate days')
    active        = models.BooleanField(default=True)
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['shelter_type', 'name']
        verbose_name = 'Shelter / Resource'
        verbose_name_plural = 'Shelters & Resources'

    def __str__(self):
        return f'{self.name} ({self.get_shelter_type_display()})'

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)
            slug, n = base, 1
            while Shelter.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f'{base}-{n}'; n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def as_map_dict(self):
        return {
            'id':           self.pk,
            'name':         self.name,
            'type':         self.shelter_type,
            'type_display': self.get_shelter_type_display(),
            'accepts':      self.get_accepts_display(),
            'pets_ok':      self.pets_ok,
            'address':      self.address,
            'phone':        self.phone,
            'website':      self.website,
            'hours':        self.hours,
            'notes':        self.notes,
            'latitude':     self.latitude,
            'longitude':    self.longitude,
            'available_hot':   self.available_hot,
            'available_cold':  self.available_cold,
            'available_smoke': self.available_smoke,
        }


class InstagramAccount(models.Model):
    """A public Instagram account whose posts should be periodically harvested."""
    STATUS_PENDING  = 'pending'
    STATUS_ACTIVE   = 'active'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES  = [
        (STATUS_PENDING,  'Pending review'),
        (STATUS_ACTIVE,   'Active — harvest posts'),
        (STATUS_REJECTED, 'Rejected — skip'),
    ]

    handle           = models.CharField(max_length=100, unique=True,
                                        help_text='Handle without @, e.g. rave.pdx')
    promoter_profile = models.OneToOneField(
        'PromoterProfile', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='instagram_account', help_text='Linked promoter/crew profile'
    )
    artist           = models.OneToOneField(
        'Artist', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='instagram_account', help_text='Linked artist profile'
    )
    ig_user_id      = models.CharField(max_length=50, blank=True)
    display_name    = models.CharField(max_length=200, blank=True)
    bio             = models.TextField(blank=True)
    follower_count  = models.IntegerField(null=True, blank=True)
    status          = models.CharField(max_length=20, choices=STATUS_CHOICES,
                                       default=STATUS_ACTIVE,
                                       help_text='Active accounts are harvested; pending awaits review')
    last_fetched    = models.DateTimeField(null=True, blank=True)
    is_active       = models.BooleanField(default=True)
    notes           = models.TextField(blank=True, help_text='Internal notes about this account')
    harvest_for_events = models.BooleanField(
        default=False,
        help_text='Run moondream flyer scan on new posts — creates pending Events from detected flyers'
    )

    class Meta:
        ordering = ['handle']
        verbose_name = 'Instagram Account'
        verbose_name_plural = 'Instagram Accounts'

    def __str__(self):
        return f'@{self.handle}'


class InstagramPost(models.Model):
    """A single post harvested from a tracked Instagram account."""
    account     = models.ForeignKey(InstagramAccount, on_delete=models.CASCADE,
                                    related_name='posts')
    ig_post_id  = models.CharField(max_length=100, unique=True)
    shortcode   = models.CharField(max_length=100, unique=True)
    caption     = models.TextField(blank=True)
    image_url   = models.URLField(max_length=1000, blank=True)
    is_video    = models.BooleanField(default=False)
    posted_at   = models.DateTimeField()
    fetched_at  = models.DateTimeField(auto_now_add=True)
    flyer_scanned   = models.BooleanField(default=False)
    flyer_result    = models.JSONField(null=True, blank=True, help_text='Raw moondream output — dict of extracted event fields')
    sourced_event   = models.ForeignKey('Event', null=True, blank=True,
                                        on_delete=models.SET_NULL, related_name='instagram_sources',
                                        help_text='Event created from this post by flyer scan')
    tagged_handles  = models.JSONField(default=list, blank=True,
                                       help_text='Instagram handles tagged in this post')

    class Meta:
        ordering = ['-posted_at']
        verbose_name = 'Instagram Post'
        verbose_name_plural = 'Instagram Posts'

    def __str__(self):
        return f'@{self.account.handle} — {self.shortcode}'

    @property
    def permalink(self):
        return f'https://www.instagram.com/p/{self.shortcode}/'


class FlyerBackground(models.Model):
    """Reusable background images for the event flyer generator. Max 10 per user."""
    owner      = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='flyer_backgrounds')
    image      = models.ImageField(upload_to='flyer_backgrounds/%Y/', blank=True)
    source_url = models.URLField(blank=True, max_length=500)  # Drive or remote URL alternative
    label      = models.CharField(max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Flyer Background'
        verbose_name_plural = 'Flyer Backgrounds'

    def __str__(self):
        return self.label or f'Background #{self.pk}'

    @property
    def bg_url(self):
        if self.image:
            try:
                return self.image.url
            except ValueError:
                pass
        if self.source_url:
            # Convert Google Drive share links → thumbnail URL
            m = re.search(r'/d/([a-zA-Z0-9_-]+)', self.source_url)
            if m:
                return f'https://drive.google.com/thumbnail?id={m.group(1)}&sz=w1200'
            m = re.search(r'id=([a-zA-Z0-9_-]+)', self.source_url)
            if m:
                return f'https://drive.google.com/thumbnail?id={m.group(1)}&sz=w1200'
            return self.source_url
        return ''


class UserPlaylist(models.Model):
    """A saved video queue generated by the profile shuffle feature."""
    user       = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='playlists')
    name       = models.CharField(max_length=200)
    items      = models.JSONField(default=list, help_text='List of {video_id, title, artist} dicts')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.name


class CronStatus(models.Model):
    """Proxy model — no DB table. Used only to hang a custom admin page off."""
    class Meta:
        managed = False
        verbose_name = 'Cron Status'
        verbose_name_plural = 'Cron Status'
        app_label = 'events'

class WorkerTask(models.Model):
    """Async task queue — processed by Unraid pull-worker, fallback on Plesk."""
    TASK_TYPES = [
        ("geocode_event", "Geocode Event"),
        ("geocode_venue", "Geocode Venue"),
        ("post_bluesky",  "Post to Bluesky"),
    ]
    STATUSES = [
        ("queued",  "Queued"),
        ("running", "Running"),
        ("done",    "Done"),
        ("error",   "Error"),
    ]
    task_type    = models.CharField(max_length=50, choices=TASK_TYPES, db_index=True)
    payload      = models.JSONField(default=dict)
    status       = models.CharField(max_length=20, choices=STATUSES, default="queued", db_index=True)
    result       = models.JSONField(null=True, blank=True)
    error_msg    = models.TextField(blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["status", "task_type"])]

    def __str__(self):
        return f"{self.task_type} [{self.status}] #{self.pk}"


class VideoRoomMessage(models.Model):
    """Chat messages for the video theater room."""
    user         = models.ForeignKey('auth.User', null=True, blank=True, on_delete=models.SET_NULL)
    display_name = models.CharField(max_length=40, blank=True)
    content      = models.CharField(max_length=400)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        indexes  = [models.Index(fields=['created_at'])]

    @property
    def author(self):
        if self.user_id:
            return self.user.username
        return self.display_name or 'anon'

    def __str__(self):
        return f'{self.author}: {self.content[:40]}'
