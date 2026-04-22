from django.db import models
from django.utils.text import slugify


class BannerMessage(models.Model):
    text       = models.CharField(max_length=500)
    active     = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Banner Message'

    def __str__(self):
        return self.text[:60]


CATEGORY_CHOICES = [
    ('general',  'General'),
    ('aid',      'Aid & Mutual Aid'),
    ('announce', 'Announcement'),
    ('question', 'Question'),
    ('offer',    'Free & Trade'),
]


class Topic(models.Model):
    title           = models.CharField(max_length=200)
    body            = models.TextField()
    author_name     = models.CharField(max_length=80)
    category        = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='general')
    pinned          = models.BooleanField(default=False)
    recurring_event = models.ForeignKey(
        'events.RecurringEvent', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='board_topics',
    )
    neighborhood = models.ForeignKey(
        'events.Neighborhood', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='topics',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-pinned', '-created_at']

    def __str__(self):
        return self.title

    def get_slug(self):
        return slugify(self.title) or 'topic'

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('board_topic', kwargs={'pk': self.pk, 'slug': self.get_slug()})


class Reply(models.Model):
    topic       = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name='replies')
    body        = models.TextField()
    author_name = models.CharField(max_length=80)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"Reply by {self.author_name} on '{self.topic.title}'"


class Offering(models.Model):
    CATEGORY_GIVE  = 'give'
    CATEGORY_TRADE = 'trade'
    CATEGORY_ISO   = 'iso'
    CATEGORY_CHOICES = [
        ('give',  'Free — Take It'),
        ('trade', 'Trade / Swap'),
        ('iso',   'In Search Of'),
    ]

    title        = models.CharField(max_length=200)
    body         = models.TextField(blank=True, help_text='Describe the item — condition, size, pickup info…')
    category     = models.CharField(max_length=10, choices=CATEGORY_CHOICES, default='give')
    photo        = models.ImageField(upload_to='offerings/', null=True, blank=True)
    contact_hint = models.CharField(max_length=200, blank=True,
                                    help_text='How to reach you — e.g. "reply to this thread" or "DM on IG @handle"')
    neighborhood = models.ForeignKey(
        'events.Neighborhood', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='offerings',
    )
    author_name  = models.CharField(max_length=80)
    poster_user  = models.ForeignKey(
        'auth.User', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='offerings',
    )
    poster_ip    = models.GenericIPAddressField(null=True, blank=True)
    is_claimed   = models.BooleanField(default=False)
    claimed_at   = models.DateTimeField(null=True, blank=True)
    board_topic  = models.OneToOneField(
        Topic, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='offering',
    )
    active     = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'[{self.get_category_display()}] {self.title}'

    def get_slug(self):
        return slugify(self.title) or 'item'

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('give_detail', kwargs={'pk': self.pk, 'slug': self.get_slug()})

    @property
    def is_expired(self):
        from django.utils import timezone
        return timezone.now() > self.expires_at
