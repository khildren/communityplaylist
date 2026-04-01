from django.contrib import admin
from django.utils.html import format_html, mark_safe
from django.utils.timezone import localtime
from django.template.response import TemplateResponse
from django import forms
from .models import Event, EventPhoto, VenueFeed, CalendarFeed, Genre, Artist, RecurringEvent, CronStatus
import os
import datetime
import requests

DISCORD_EVENTS = "https://discord.com/api/webhooks/1487258605102039051/aMDBINHJSRTE2DVRB7AIdEQpC-5pacJgEKwEn9_gf6nhJbCLlsXD41zADDIlP-5Md5CC"
LOGO = "https://hihi.communityplaylist.com/files/timeline_files/store_file6809b5ed4135d-community_playlist_site_logo_2025.png"

def post_to_discord_events(event):
    try:
        genres = ', '.join(event.genres.values_list('name', flat=True)) or 'Various'
        image_url = f"https://communityplaylist.com{event.photo.url}" if event.photo else LOGO
        payload = {
            "embeds": [{
                "title": event.title,
                "url": f"https://communityplaylist.com/events/{event.slug}/",
                "description": event.description[:200] + '...' if len(event.description) > 200 else event.description,
                "color": 0xff6b35,
                "fields": [
                    {"name": "📅 Date", "value": localtime(event.start_date).strftime('%A, %B %d %Y @ %I:%M %p'), "inline": True},
                    {"name": "📍 Location", "value": event.location[:100], "inline": True},
                    {"name": "🎵 Genre", "value": genres, "inline": True},
                    {"name": "💰 Cost", "value": "FREE" if event.is_free else event.price_info or "Paid", "inline": True},
                ],
                "thumbnail": {"url": image_url},
                "footer": {"text": "communityplaylist.com — PDX community events"}
            }]
        }
        requests.post(DISCORD_EVENTS, json=payload)
    except Exception as e:
        print(f"Discord notify error: {e}")


class EventPhotoInline(admin.TabularInline):
    model = EventPhoto
    extra = 0
    fields = ['image', 'caption', 'photo_type', 'submitted_by', 'approved']


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    search_fields = ['name']
    ordering = ['name']


@admin.register(Artist)
class ArtistAdmin(admin.ModelAdmin):
    search_fields = ['name']
    ordering = ['name']
    list_display = ['name', 'mb_id', 'website']


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ['title', 'start_date', 'location', 'neighborhood', 'status', 'is_free', 'submitted_by']
    list_filter = ['status', 'is_free', 'neighborhood']
    search_fields = ['title', 'location', 'submitted_by']
    list_editable = ['status']
    ordering = ['start_date']
    prepopulated_fields = {'slug': ('title',)}
    autocomplete_fields = ['genres', 'artists']
    inlines = [EventPhotoInline]

    def save_model(self, request, obj, form, change):
        old_status = None
        if obj.pk:
            old_status = Event.objects.get(pk=obj.pk).status
        super().save_model(request, obj, form, change)
        if obj.status == 'approved' and old_status != 'approved':
            post_to_discord_events(obj)


@admin.register(EventPhoto)
class EventPhotoAdmin(admin.ModelAdmin):
    list_display = ['event', 'photo_type', 'submitted_by', 'approved', 'created_at']
    list_filter = ['approved', 'photo_type']
    list_editable = ['approved']


@admin.register(VenueFeed)
class VenueFeedAdmin(admin.ModelAdmin):
    list_display = ['name', 'source_type', 'active', 'auto_approve', 'default_category', 'genre_list', 'last_synced', 'health_status']
    list_filter = ['source_type', 'active', 'auto_approve', 'default_category']
    list_editable = ['active', 'auto_approve']
    search_fields = ['name', 'url', 'notes']
    ordering = ['name']
    readonly_fields = ['last_synced', 'last_error', 'created_at']
    fieldsets = (
        (None, {'fields': ('name', 'website', 'source_type', 'url')}),
        ('Import settings', {'fields': ('active', 'auto_approve', 'default_category', 'default_genres', 'residents')}),
        ('Status', {'fields': ('last_synced', 'last_error', 'created_at')}),
        ('Notes', {'fields': ('notes',)}),
    )

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name in ('default_genres', 'residents'):
            kwargs['widget'] = forms.SelectMultiple(attrs={'id': f'id_{db_field.name}'})
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    class Media:
        js = ('admin/js/venuefeed_genre.js',)

    def genre_list(self, obj):
        return ', '.join(obj.default_genres.values_list('name', flat=True)) or '—'
    genre_list.short_description = 'Default genres'

    def health_status(self, obj):
        if not obj.last_synced:
            return mark_safe('<span style="color:#888">never synced</span>')
        if obj.last_error:
            return format_html('<span style="color:#f66" title="{}">error</span>', obj.last_error[:200])
        return mark_safe('<span style="color:#4caf50">ok</span>')
    health_status.short_description = 'Health'


@admin.register(RecurringEvent)
class RecurringEventAdmin(admin.ModelAdmin):
    list_display  = ['title', 'frequency', 'interval', 'day_of_week', 'week_of_month',
                     'start_time', 'active', 'auto_approve', 'instance_count']
    list_filter   = ['active', 'auto_approve', 'frequency', 'category']
    list_editable = ['active', 'auto_approve']
    search_fields = ['title', 'location']
    ordering      = ['title']
    autocomplete_fields = ['residents', 'genres']
    fieldsets = (
        (None,         {'fields': ('title', 'description', 'location', 'category', 'photo',
                                   'is_free', 'price_info', 'website')}),
        ('Schedule',   {'fields': ('frequency', 'interval', 'day_of_week', 'week_of_month',
                                   'start_time', 'duration_minutes', 'lookahead_weeks')}),
        ('Artists',    {'fields': ('residents', 'genres')}),
        ('Admin',      {'fields': ('active', 'auto_approve', 'submitted_by', 'submitted_email')}),
    )

    def instance_count(self, obj):
        return obj.instances.count()
    instance_count.short_description = '# Events'


@admin.register(CalendarFeed)
class CalendarFeedAdmin(admin.ModelAdmin):
    list_display = ['user', 'label', 'url', 'last_synced', 'created_at']
    search_fields = ['user__email', 'label', 'url']
    readonly_fields = ['last_synced', 'created_at']


# ── Cron Status dashboard ─────────────────────────────────────────────────────

CRON_JOBS = [
    {
        'name':     'Import user feeds',
        'command':  'import_feeds',
        'log':      '/var/log/cp_import_feeds.log',
        'schedule': 'Mon + Thu  6:00 AM',
    },
    {
        'name':     'Import venue / PDX net feeds',
        'command':  'import_venue_feeds',
        'log':      '/var/log/cp_import_venues.log',
        'schedule': 'Mon + Thu  7:00 AM',
    },
    {
        'name':     'Generate recurring event instances',
        'command':  'generate_recurring_events',
        'log':      '/var/log/cp_recurring.log',
        'schedule': 'Daily  6:05 AM',
    },
    {
        'name':     'Geocode events (20/night)',
        'command':  'geocode_events',
        'log':      '/var/log/cp_geocode.log',
        'schedule': 'Daily  2:00 AM',
    },
    {
        'name':     'Fetch event images (og:image)',
        'command':  'fetch_event_images',
        'log':      '/var/log/cp_fetch_images.log',
        'schedule': 'Daily  3:00 AM',
    },
    {
        'name':     'Daily Discord digest',
        'command':  'daily_digest',
        'log':      '/var/log/cp_daily_digest.log',
        'schedule': 'Daily  9:00 AM',
    },
    {
        'name':     'Recheck inactive venue feeds',
        'command':  'recheck_venue_feeds',
        'log':      '/var/log/cp_recheck_feeds.log',
        'schedule': '1st of month  8:00 AM',
    },
    {
        'name':     'Discover new PDX feeds',
        'command':  'discover_pdx_feeds',
        'log':      '/var/log/cp_discover_feeds.log',
        'schedule': '1st of month  8:05 AM',
    },
]


def _parse_log(path, tail_lines=25):
    """Read a log file. Returns (mtime_dt, last_lines, has_error)."""
    if not os.path.exists(path):
        return None, [], False
    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path))
    try:
        with open(path, 'r', errors='replace') as f:
            lines = f.readlines()
    except Exception:
        return mtime, [], False
    last = [l.rstrip() for l in lines[-tail_lines:] if l.strip()]
    has_error = any(
        kw in l
        for l in lines[-30:]
        for kw in ('Traceback', 'SyntaxError', 'ImportError', 'ERROR:', 'Error:', 'FAILED')
    )
    return mtime, last, has_error


@admin.register(CronStatus)
class CronStatusAdmin(admin.ModelAdmin):
    """Custom admin page — no DB table, just reads cron log files."""

    def has_add_permission(self, _request):
        return False

    def has_delete_permission(self, _request, _obj=None):
        return False

    def has_change_permission(self, _request, _obj=None):
        return False

    def changelist_view(self, request, _extra_context=None):
        if not request.user.is_staff:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied

        jobs = []
        for job in CRON_JOBS:
            mtime, lines, has_error = _parse_log(job['log'])
            jobs.append({
                **job,
                'mtime':     mtime,
                'lines':     lines,
                'has_error': has_error,
                'exists':    mtime is not None,
            })

        context = {
            **self.admin_site.each_context(request),
            'title': 'Cron Status',
            'jobs':  jobs,
            'now':   datetime.datetime.now(),
        }
        return TemplateResponse(request, 'admin/cron_status.html', context)