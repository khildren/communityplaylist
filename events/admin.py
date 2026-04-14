from django.contrib import admin
from django.utils.html import format_html, mark_safe
from django.utils.timezone import localtime
from django.template.response import TemplateResponse
from django.urls import path
from django.http import HttpResponseRedirect
from django.contrib import messages
from django import forms
from .models import Event, EventPhoto, VenueFeed, CalendarFeed, Genre, Artist, RecurringEvent, CronStatus, Venue, EditSuggestion, Neighborhood, UserProfile
import os
import datetime
import subprocess
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


def _scrape_venue_site(website):
    """
    Fetch a venue website and extract:
      - address: from schema.org JSON-LD or meta tags
      - logo_url: og:image or apple-touch-icon or largest favicon
    Returns dict with keys 'address' and 'logo_url' (either may be None).
    """
    import re
    from urllib.parse import urljoin
    _UA = {'User-Agent': 'Mozilla/5.0 (compatible; CommunityPlaylist/1.0; +https://communityplaylist.com)'}
    result = {'address': None, 'logo_url': None}
    try:
        r = requests.get(website, timeout=8, headers=_UA, allow_redirects=True)
        if r.status_code != 200:
            return result
        html = r.text
        base = r.url

        # ── Address: schema.org JSON-LD ──────────────────────────────────────
        import json
        for ld_raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S | re.I):
            try:
                ld = json.loads(ld_raw)
                nodes = ld if isinstance(ld, list) else [ld]
                for node in nodes:
                    addr = node.get('address') or {}
                    if isinstance(addr, str) and addr.strip():
                        result['address'] = addr.strip()[:300]
                        break
                    street = addr.get('streetAddress', '')
                    city   = addr.get('addressLocality', '')
                    state  = addr.get('addressRegion', '')
                    zipcode= addr.get('postalCode', '')
                    if street:
                        parts = [p for p in [street, city, state, zipcode] if p]
                        result['address'] = ', '.join(parts)[:300]
                        break
                if result['address']:
                    break
            except Exception:
                pass

        # ── Logo: og:image first, then apple-touch-icon ──────────────────────
        og = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if not og:
            og = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html, re.I)
        if og:
            result['logo_url'] = urljoin(base, og.group(1).strip())
        else:
            touch = re.search(r'<link[^>]+rel=["\'][^"\']*apple-touch-icon[^"\']*["\'][^>]+href=["\']([^"\']+)["\']', html, re.I)
            if touch:
                result['logo_url'] = urljoin(base, touch.group(1).strip())

    except Exception:
        pass
    return result


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    list_display  = ['name', 'neighborhood', 'verified', 'claimed_by', 'created_at']
    list_filter   = ['verified', 'active']
    search_fields = ['name', 'address', 'neighborhood']
    ordering      = ['name']
    readonly_fields = ['created_at']
    actions = ['verify_venues', 'autofill_from_website', 'close_venue']

    def verify_venues(self, request, queryset):
        queryset.update(verified=True)
        self.message_user(request, f'{queryset.count()} venue(s) verified.')
    verify_venues.short_description = 'Mark selected venues as verified'

    def close_venue(self, request, queryset):
        import datetime
        today = datetime.date.today()
        cancelled_events = 0
        disabled_feeds   = 0
        for venue in queryset:
            venue.active      = False
            venue.closed_date = venue.closed_date or today
            venue.verified    = False
            venue.save()
            # Disable the linked VenueFeed so no more events import
            if venue.venue_feed_id:
                from events.models import VenueFeed
                VenueFeed.objects.filter(pk=venue.venue_feed_id).update(active=False)
                disabled_feeds += 1
            # Cancel all future approved/pending events at this address
            if venue.address:
                from django.utils import timezone as tz
                n = Event.objects.filter(
                    location__icontains=venue.address[:30],
                    start_date__gte=tz.now(),
                    status__in=('pending', 'approved'),
                ).update(status='cancelled')
                cancelled_events += n
        msg = (
            f'{queryset.count()} venue(s) closed · '
            f'{cancelled_events} future event(s) cancelled · '
            f'{disabled_feeds} feed(s) disabled.'
        )
        self.message_user(request, msg)
    close_venue.short_description = '🔒 Mark venue as permanently closed (cancel future events)'

    def autofill_from_website(self, request, queryset):
        filled = 0
        for venue in queryset:
            if not venue.website:
                continue
            data = _scrape_venue_site(venue.website)
            changed = False
            if data['address'] and not venue.address:
                venue.address = data['address']
                changed = True
            if data['logo_url'] and not venue.logo:
                try:
                    img_r = requests.get(data['logo_url'], timeout=8,
                        headers={'User-Agent': 'Mozilla/5.0'})
                    if img_r.status_code == 200:
                        import mimetypes
                        from django.core.files.base import ContentFile
                        ct = img_r.headers.get('content-type', '').split(';')[0].strip()
                        ext = mimetypes.guess_extension(ct) or '.jpg'
                        ext = ext.replace('.jpe', '.jpg')
                        from django.utils.text import slugify
                        fname = f"venue_{slugify(venue.name)}{ext}"
                        venue.logo.save(fname, ContentFile(img_r.content), save=False)
                        changed = True
                except Exception:
                    pass
            if changed:
                # Also geocode if address was just filled
                if data['address'] and not venue.latitude:
                    from events.geocode import geocode_location, reverse_geocode_neighborhood
                    lat, lng = geocode_location(venue.address)
                    if lat:
                        venue.latitude, venue.longitude = lat, lng
                        hood = reverse_geocode_neighborhood(lat, lng)
                        if hood and not venue.neighborhood:
                            venue.neighborhood = hood
                venue.save()
                filled += 1
        self.message_user(request, f'Auto-filled {filled} venue(s) from their websites.')
    autofill_from_website.short_description = 'Auto-fill address & logo from website'

    def save_model(self, request, obj, form, change):
        # Auto-scrape website when address or logo is blank
        if obj.website and (not obj.address or not obj.logo):
            data = _scrape_venue_site(obj.website)
            if data['address'] and not obj.address:
                obj.address = data['address']
                messages.info(request, f'Address auto-filled from website: {obj.address}')
            if data['logo_url'] and not obj.logo:
                try:
                    img_r = requests.get(data['logo_url'], timeout=8,
                        headers={'User-Agent': 'Mozilla/5.0'})
                    if img_r.status_code == 200:
                        import mimetypes
                        from django.core.files.base import ContentFile
                        ct = img_r.headers.get('content-type', '').split(';')[0].strip()
                        ext = mimetypes.guess_extension(ct) or '.jpg'
                        ext = ext.replace('.jpe', '.jpg')
                        from django.utils.text import slugify
                        fname = f"venue_{slugify(obj.name)}{ext}"
                        obj.logo.save(fname, ContentFile(img_r.content), save=False)
                        messages.info(request, 'Logo auto-filled from website.')
                except Exception:
                    pass
        # Geocode address if coords missing
        if obj.address and not obj.latitude:
            from events.geocode import geocode_location, reverse_geocode_neighborhood
            lat, lng = geocode_location(obj.address)
            if lat:
                obj.latitude, obj.longitude = lat, lng
                if not obj.neighborhood:
                    obj.neighborhood = reverse_geocode_neighborhood(lat, lng)
                messages.info(request, f'Geocoded: {lat:.4f}, {lng:.4f}')
        super().save_model(request, obj, form, change)


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

    def changelist_view(self, request, extra_context=None):
        """Prepend a published-feeds directory above the normal list."""
        SITE = 'https://communityplaylist.com'

        # ── Site-wide iCal feeds ──────────────────────────────────────────
        published = [
            {
                'group': 'Site-wide',
                'name': 'All approved events',
                'url': f'{SITE}/feed/events.ics',
                'desc': 'Every upcoming approved event',
                'type': 'ical',
            },
            {
                'group': 'Site-wide',
                'name': 'All events — Music',
                'url': f'{SITE}/feed/events.ics?category=music',
                'desc': 'Upcoming music events only',
                'type': 'ical',
            },
            {
                'group': 'Site-wide',
                'name': 'All events — Free only',
                'url': f'{SITE}/feed/events.ics?free=1',
                'desc': 'Free upcoming events only',
                'type': 'ical',
            },
        ]

        # ── Per-venue iCal feeds ──────────────────────────────────────────
        for v in Venue.objects.filter(active=True).order_by('name'):
            published.append({
                'group': 'Venues',
                'name': v.name,
                'url': f'{SITE}/venues/{v.slug}/feed.ics',
                'desc': v.neighborhood or '',
                'type': 'ical',
            })

        # ── Per-user RSS feeds ────────────────────────────────────────────
        for p in UserProfile.objects.filter(is_public=True).select_related('user').order_by('handle'):
            if p.handle:
                published.append({
                    'group': 'User profiles',
                    'name': f'@{p.handle}',
                    'url': f'{SITE}/u/@{p.handle}/feed/',
                    'desc': p.bio[:60] if p.bio else '',
                    'type': 'rss',
                })

        extra_context = extra_context or {}
        extra_context['published_feeds'] = published
        return super().changelist_view(request, extra_context=extra_context)


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


@admin.register(Neighborhood)
class NeighborhoodAdmin(admin.ModelAdmin):
    list_display  = ['name', 'slug', 'aliases', 'active']
    list_editable = ['active']
    search_fields = ['name', 'aliases']


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display  = ['handle', 'user', 'pronouns', 'is_public', 'email_verified', 'created_at']
    list_filter   = ['is_public', 'email_verified']
    search_fields = ['handle', 'user__email']
    readonly_fields = ['created_at']


def _approve_suggestions(modeladmin, request, queryset):
    applied, skipped = 0, 0
    for s in queryset.filter(status=EditSuggestion.STATUS_PENDING):
        if s.apply():
            s.status = EditSuggestion.STATUS_APPROVED
            s.reviewed_by = request.user
            s.save(update_fields=['status', 'reviewed_by'])
            applied += 1
        else:
            skipped += 1
    modeladmin.message_user(request, f'{applied} applied, {skipped} skipped (target not found).')
_approve_suggestions.short_description = 'Approve & apply selected suggestions'


def _reject_suggestions(modeladmin, request, queryset):
    updated = queryset.filter(status=EditSuggestion.STATUS_PENDING).update(
        status=EditSuggestion.STATUS_REJECTED,
        reviewed_by=request.user,
    )
    modeladmin.message_user(request, f'{updated} rejected.')
_reject_suggestions.short_description = 'Reject selected suggestions'


@admin.register(EditSuggestion)
class EditSuggestionAdmin(admin.ModelAdmin):
    list_display  = ['__str__', 'user', 'status', 'created_at', 'target_link']
    list_filter   = ['status', 'target_type']
    search_fields = ['user__email', 'suggested_value', 'note']
    readonly_fields = ['user', 'target_type', 'target_id', 'field_name',
                       'current_value', 'suggested_value', 'note', 'created_at', 'target_link']
    actions = [_approve_suggestions, _reject_suggestions]
    ordering = ['-created_at']

    def target_link(self, obj):
        target = obj.get_target()
        if not target:
            return '—'
        if obj.target_type == 'event':
            return format_html('<a href="/events/{}/" target="_blank">{}</a>', target.slug, target.title)
        if obj.target_type == 'venue':
            return format_html('<a href="/venues/{}/" target="_blank">{}</a>', target.slug, target.name)
        if obj.target_type == 'artist':
            return format_html('<a href="/artists/{}/" target="_blank">{}</a>', target.pk, target.name)
        if obj.target_type == 'neighborhood':
            return format_html('<a href="/neighborhoods/{}/" target="_blank">{}</a>', target.slug, target.name)
        return '—'
    target_link.short_description = 'Target'


def _build_alerts():
    """Collect actionable items needing attention."""
    from board.models import Topic
    alerts = []

    pending_events = Event.objects.filter(status='pending').count()
    if pending_events:
        alerts.append({
            'level': 'warn',
            'icon': '📋',
            'label': f'{pending_events} event{"s" if pending_events != 1 else ""} pending approval',
            'url': '/admin/events/event/?status__exact=pending',
        })

    pending_edits = EditSuggestion.objects.filter(status=EditSuggestion.STATUS_PENDING).count()
    if pending_edits:
        alerts.append({
            'level': 'warn',
            'icon': '✏️',
            'label': f'{pending_edits} edit suggestion{"s" if pending_edits != 1 else ""} to review',
            'url': '/admin/events/editsuggestion/?status__exact=pending',
        })

    pending_photos = EventPhoto.objects.filter(approved=False).count()
    if pending_photos:
        alerts.append({
            'level': 'warn',
            'icon': '🖼',
            'label': f'{pending_photos} event photo{"s" if pending_photos != 1 else ""} awaiting approval',
            'url': '/admin/events/eventphoto/?approved__exact=0',
        })

    claimed_unverified = Venue.objects.filter(verified=False, claimed_by__isnull=False).count()
    if claimed_unverified:
        alerts.append({
            'level': 'info',
            'icon': '🏛',
            'label': f'{claimed_unverified} claimed venue{"s" if claimed_unverified != 1 else ""} not yet verified',
            'url': '/admin/events/venue/?verified__exact=0',
        })

    spam_topics = Topic.objects.filter(flagged=True).count() if hasattr(Topic, 'flagged') else 0
    if spam_topics:
        alerts.append({
            'level': 'error',
            'icon': '🚫',
            'label': f'{spam_topics} flagged board topic{"s" if spam_topics != 1 else ""}',
            'url': '/admin/board/topic/',
        })

    return alerts


_COMMAND_MAP = {job['command']: job for job in CRON_JOBS}
_COMMAND_APP = {
    'import_feeds':              'events',
    'import_venue_feeds':        'events',
    'generate_recurring_events': 'events',
    'geocode_events':            'events',
    'fetch_event_images':        'events',
    'daily_digest':              'events',
    'recheck_venue_feeds':       'events',
    'discover_pdx_feeds':        'events',
    'sweep_spam_topics':         'board',
}


@admin.register(CronStatus)
class CronStatusAdmin(admin.ModelAdmin):
    """Custom admin page — no DB table, just reads cron log files."""

    def has_add_permission(self, _request):
        return False

    def has_delete_permission(self, _request, _obj=None):
        return False

    def has_change_permission(self, _request, _obj=None):
        return False

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path('retry/<command>/', self.admin_site.admin_view(self._retry_view), name='cron_retry'),
        ]
        return custom + urls

    def _retry_view(self, request, command):
        if not request.user.is_superuser:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        if command not in _COMMAND_MAP:
            messages.error(request, f'Unknown command: {command}')
            return HttpResponseRedirect('/admin/events/cronstatus/')

        _BASE = '/var/www/vhosts/communityplaylist.com/django'
        _PYTHON = os.path.join(_BASE, 'venv/bin/python3')
        _MANAGE = os.path.join(_BASE, 'manage.py')

        try:
            # Fire-and-forget — do NOT wait (subprocess.run blocks gunicorn worker
            # until the master kills it). The cron log file shows progress.
            subprocess.Popen(
                [_PYTHON, _MANAGE, command],
                cwd=_BASE,
                env={**os.environ, 'DJANGO_SETTINGS_MODULE': 'communityplaylist.settings'},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,  # detach from gunicorn process group
            )
            messages.success(request, f'✓ "{command}" launched — reload this page in a moment to see updated log output.')
        except Exception as e:
            messages.error(request, f'Failed to launch "{command}": {e}')
        return HttpResponseRedirect('/admin/events/cronstatus/')

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

        alerts = _build_alerts()

        context = {
            **self.admin_site.each_context(request),
            'title': 'Cron Status',
            'jobs':  jobs,
            'alerts': alerts,
            'now':   datetime.datetime.now(),
        }
        return TemplateResponse(request, 'admin/cron_status.html', context)