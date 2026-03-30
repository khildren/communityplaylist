from django.contrib import admin
from django.utils.html import format_html, mark_safe
from .models import Event, EventPhoto, VenueFeed, CalendarFeed
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
                    {"name": "📅 Date", "value": event.start_date.strftime('%A, %B %d %Y @ %I:%M %p'), "inline": True},
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


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ['title', 'start_date', 'location', 'neighborhood', 'status', 'is_free', 'submitted_by']
    list_filter = ['status', 'is_free', 'neighborhood']
    search_fields = ['title', 'location', 'submitted_by']
    list_editable = ['status']
    ordering = ['start_date']
    prepopulated_fields = {'slug': ('title',)}
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
    list_display = ['name', 'source_type', 'active', 'auto_approve', 'default_category', 'last_synced', 'health_status']
    list_filter = ['source_type', 'active', 'auto_approve', 'default_category']
    list_editable = ['active', 'auto_approve']
    search_fields = ['name', 'url', 'notes']
    ordering = ['name']
    readonly_fields = ['last_synced', 'last_error', 'created_at']
    fieldsets = (
        (None, {'fields': ('name', 'website', 'source_type', 'url')}),
        ('Import settings', {'fields': ('active', 'auto_approve', 'default_category')}),
        ('Status', {'fields': ('last_synced', 'last_error', 'created_at')}),
        ('Notes', {'fields': ('notes',)}),
    )

    def health_status(self, obj):
        if not obj.last_synced:
            return mark_safe('<span style="color:#888">never synced</span>')
        if obj.last_error:
            return format_html('<span style="color:#f66" title="{}">error</span>', obj.last_error[:200])
        return mark_safe('<span style="color:#4caf50">ok</span>')
    health_status.short_description = 'Health'


@admin.register(CalendarFeed)
class CalendarFeedAdmin(admin.ModelAdmin):
    list_display = ['user', 'label', 'url', 'last_synced', 'created_at']
    search_fields = ['user__email', 'label', 'url']
    readonly_fields = ['last_synced', 'created_at']