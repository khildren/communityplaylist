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

    def save(self, *args, **kwargs):
        # Track whether is_claimed just flipped to True
        just_claimed = False
        if self.pk and self.is_claimed:
            just_claimed = not Offering.objects.filter(pk=self.pk, is_claimed=True).exists()
        super().save(*args, **kwargs)
        if just_claimed:
            try:
                from events.models import CommunityAsk
                CommunityAsk.objects.filter(board_offering=self).update(
                    status=CommunityAsk.STATUS_FULFILLED,
                )
            except Exception:
                pass


class SocialQueue(models.Model):
    """Pending social media posts. Processed by flush_social_queue management command."""
    TARGET_TOPIC    = 'topic'
    TARGET_OFFERING = 'offering'
    TARGET_EVENT    = 'event'
    TARGET_CHOICES  = [('topic','Topic'),('offering','Offering'),('event','Event')]

    STATUS_QUEUED  = 'queued'
    STATUS_POSTED  = 'posted'
    STATUS_FAILED  = 'failed'
    STATUS_SKIPPED = 'skipped'
    STATUS_CHOICES = [
        ('queued','Queued'),('posted','Posted'),
        ('failed','Failed'),('skipped','Skipped'),
    ]

    target_type = models.CharField(max_length=20, choices=TARGET_CHOICES)
    target_id   = models.PositiveIntegerField()
    status      = models.CharField(max_length=10, choices=STATUS_CHOICES, default='queued')
    post_after  = models.DateTimeField()
    bluesky_uri = models.CharField(max_length=200, blank=True)
    error       = models.TextField(blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    posted_at   = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['post_after']

    def __str__(self):
        return f'SocialQueue {self.target_type}#{self.target_id} [{self.status}]'


class PostReport(models.Model):
    TARGET_TOPIC   = 'topic'
    TARGET_REPLY   = 'reply'
    TARGET_OFFERING = 'offering'
    TARGET_CHOICES = [
        ('topic',    'Topic'),
        ('reply',    'Reply'),
        ('offering', 'Offering'),
    ]

    REASON_SPAM    = 'spam'
    REASON_HARMFUL = 'harmful'
    REASON_WRONG   = 'wrong_section'
    REASON_MISINFO = 'misinfo'
    REASON_OTHER   = 'other'
    REASON_CHOICES = [
        ('spam',         'Spam or scam'),
        ('harmful',      'Inappropriate or harmful content'),
        ('wrong_section','Posted in the wrong section'),
        ('misinfo',      'Misinformation'),
        ('other',        'Other'),
    ]

    target_type  = models.CharField(max_length=20, choices=TARGET_CHOICES)
    target_id    = models.PositiveIntegerField()
    reason       = models.CharField(max_length=30, choices=REASON_CHOICES)
    note         = models.TextField(blank=True, max_length=500)
    reporter_ip  = models.GenericIPAddressField(null=True, blank=True)
    reporter_user = models.ForeignKey(
        'auth.User', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='post_reports',
    )
    resolved     = models.BooleanField(default=False)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Report: {self.target_type} #{self.target_id} — {self.get_reason_display()}'

    def get_target_url(self):
        try:
            if self.target_type == 'topic':
                obj = Topic.objects.get(pk=self.target_id)
                return obj.get_absolute_url()
            if self.target_type == 'offering':
                obj = Offering.objects.get(pk=self.target_id)
                return obj.get_absolute_url()
        except Exception:
            pass
        return None
