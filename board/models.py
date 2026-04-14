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
