from django.contrib import admin
from django.utils.html import format_html, mark_safe
from django.utils.timezone import localtime
from django.template.response import TemplateResponse
from django.urls import path
from django.http import HttpResponseRedirect, StreamingHttpResponse
from django.contrib import messages
from django import forms
from .models import Event, EventPhoto, VenueFeed, CalendarFeed, Genre, Artist, RecurringEvent, CronStatus, Venue, EditSuggestion, Neighborhood, UserProfile, PromoterProfile, PlaylistTrack, RecordListing, RecordReservation, VideoTrack, Shelter, InstagramAccount, InstagramPost, WorkerTask
import os
import datetime
import subprocess
import requests

LOGO = "https://hihi.communityplaylist.com/files/timeline_files/store_file6809b5ed4135d-community_playlist_site_logo_2025.png"


class EventPhotoInline(admin.TabularInline):
    model = EventPhoto
    extra = 0
    fields = ['image', 'caption', 'photo_type', 'submitted_by', 'approved']


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    search_fields = ['name']
    ordering = ['name']


def _artist_score(a):
    """Higher = more canonical. Used to pick the winner when merging duplicates."""
    return (
        bool(a.claimed_by) * 1000 +
        a.is_verified * 500 +
        bool(a.photo) * 50 +
        bool(a.bio) * 30 +
        bool(a.instagram or a.soundcloud or a.mixcloud or a.youtube or a.spotify) * 20 +
        bool(a.drive_folder_url) * 10 +
        bool(a.mb_id) * 5 -
        a.pk  # tie-break: prefer older (lower pk)
    )


def merge_artists(modeladmin, request, queryset):
    """Merge selected Artist records into the most canonical one."""
    artists = list(queryset)
    if len(artists) < 2:
        modeladmin.message_user(request, 'Select at least 2 artists to merge.', messages.WARNING)
        return

    winner = max(artists, key=_artist_score)
    losers = [a for a in artists if a.pk != winner.pk]

    for loser in losers:
        # Reassign all M2M relations from loser → winner
        for event in loser.events.all():
            event.artists.remove(loser)
            event.artists.add(winner)
        for promo in loser.crews.all():
            promo.members.remove(loser)
            promo.members.add(winner)
        for recurring in loser.recurring_events.all():
            recurring.residents.remove(loser)
            recurring.residents.add(winner)
        # Carry over profile fields winner is missing
        for field in ('bio', 'photo', 'website', 'mb_id', 'instagram', 'soundcloud',
                      'bandcamp', 'mixcloud', 'youtube', 'spotify', 'mastodon',
                      'bluesky', 'tiktok', 'drive_folder_url'):
            if not getattr(winner, field) and getattr(loser, field):
                setattr(winner, field, getattr(loser, field))
        if not winner.claimed_by and loser.claimed_by:
            winner.claimed_by = loser.claimed_by
        loser.delete()

    winner.save()
    modeladmin.message_user(
        request,
        f'Merged {len(losers)} duplicate(s) into "{winner.name}" (pk={winner.pk}).',
        messages.SUCCESS,
    )

merge_artists.short_description = 'Merge selected artists into the most canonical one'


@admin.register(Artist)
class ArtistAdmin(admin.ModelAdmin):
    search_fields = ['name', 'slug', 'city', 'home_neighborhood']
    ordering = ['name']
    list_display  = ['name', 'slug', 'stub_badge', 'show_count', 'home_neighborhood',
                     'city', 'has_drive', 'is_verified', 'claimed_by', 'last_enriched_at']
    list_editable = ['is_verified']
    list_filter   = ['is_verified', 'is_stub', 'claimed_by']
    raw_id_fields = ['claimed_by']
    actions       = [merge_artists, 'rebuild_stubs', 'mark_not_stub', 'convert_to_crew', 'retire_as_crew']
    readonly_fields = ['is_stub', 'auto_bio', 'home_neighborhood', 'city',
                       'latitude', 'longitude', 'last_enriched_at']
    raw_id_fields = ['claimed_by', 'linked_promoter']
    change_form_template = 'admin/events/artist/change_form.html'
    fieldsets = [
        (None, {'fields': ['name', 'slug', 'photo', 'bio', 'website']}),
        ('Social links', {'fields': ['instagram', 'soundcloud', 'bandcamp', 'mixcloud',
                                     'youtube', 'spotify', 'mastodon', 'bluesky',
                                     'tiktok', 'twitch', 'beatport', 'discogs',
                                     'house_mixes', 'house_mixes_sort'], 'classes': ['collapse']}),
        ('Music folder', {'fields': ['drive_folder_url']}),
        ('Claim & verification', {'fields': ['admin_email', 'claimed_by', 'is_verified', 'is_live',
                                              'youtube_channel_id', 'view_count', 'linked_promoter']}),
        ('Auto-generated', {'fields': ['is_stub', 'auto_bio', 'home_neighborhood', 'city',
                                        'latitude', 'longitude', 'last_enriched_at'], 'classes': ['collapse']}),
    ]

    def get_urls(self):
        from django.urls import path as _path
        urls = super().get_urls()
        return [
            _path('<int:pk>/send-claim-email/',
                  self.admin_site.admin_view(self._send_claim_email_view),
                  name='events_artist_send_claim_email'),
        ] + urls

    def _send_claim_email_view(self, request, pk):
        from django.shortcuts import redirect, get_object_or_404
        from django.urls import reverse
        obj = get_object_or_404(Artist, pk=pk)
        if not obj.admin_email:
            self.message_user(request, 'No admin email set on this artist.', level='error')
        else:
            PromoterProfileAdmin._do_send_claim_email(request, obj, 'artists')
        return redirect(reverse('admin:events_artist_change', args=[pk]))

    def stub_badge(self, obj):
        if obj.is_stub and not obj.claimed_by_id:
            return '🤖 stub'
        if obj.claimed_by_id:
            return '✅ claimed'
        return '—'
    stub_badge.short_description = 'Status'

    def show_count(self, obj):
        return obj.events.filter(status='approved').count()
    show_count.short_description = 'Shows'

    def has_drive(self, obj):
        return bool(obj.drive_folder_url)
    has_drive.boolean = True
    has_drive.short_description = 'Drive'

    def rebuild_stubs(self, request, queryset):
        from django.core.management import call_command
        call_command('auto_stub_artists', '--force-refresh')
        self.message_user(request, 'Stub rebuild triggered for all qualifying artists.')
    rebuild_stubs.short_description = '🤖 Rebuild stubs (geo + auto-bio)'

    def mark_not_stub(self, request, queryset):
        updated = queryset.update(is_stub=False)
        self.message_user(request, f'{updated} artist(s) unmarked as stubs.')
    mark_not_stub.short_description = '✏️ Mark selected as real (not stub)'

    def convert_to_crew(self, request, queryset):
        """Create a PromoterProfile for each selected artist and link them (keeps Artist record)."""
        from django.utils.text import slugify as _slugify
        created = already = 0
        for artist in queryset:
            if artist.linked_promoter:
                already += 1
                continue
            slug = _slugify(artist.name)
            promoter, new = PromoterProfile.objects.get_or_create(
                slug=slug,
                defaults={
                    'name':          artist.name,
                    'promoter_type': 'crew',
                    'bio':           artist.bio or artist.auto_bio,
                    'website':       artist.website,
                    'instagram':     artist.instagram,
                    'soundcloud':    artist.soundcloud,
                    'spotify':       artist.spotify,
                    'claimed_by':    artist.claimed_by,
                }
            )
            artist.linked_promoter = promoter
            artist.save(update_fields=['linked_promoter'])
            for ev in artist.events.all():
                ev.promoters.add(promoter)
            created += 1
        self.message_user(request, f'{created} crew profile(s) created and linked, {already} already linked.')
    convert_to_crew.short_description = '🔁 Link selected artists → Crew profile (keep Artist)'

    def retire_as_crew(self, request, queryset):
        """
        Full crew migration: ensure PromoterProfile exists, move all event links,
        copy missing profile fields, then DELETE the Artist stub.

        Use this when the record is a crew/collective that was mistakenly
        imported as an individual artist (e.g. Gnosis DnB, Subduction Audio).
        """
        from django.utils.text import slugify as _slugify
        retired = skipped = 0
        for artist in queryset:
            # 1. Ensure PromoterProfile exists
            promoter = artist.linked_promoter
            if not promoter:
                slug = _slugify(artist.name)
                promoter, _ = PromoterProfile.objects.get_or_create(
                    slug=slug,
                    defaults={
                        'name':          artist.name,
                        'promoter_type': 'crew',
                        'bio':           artist.bio or artist.auto_bio,
                        'website':       artist.website,
                        'instagram':     artist.instagram,
                        'soundcloud':    artist.soundcloud,
                        'spotify':       artist.spotify,
                        'claimed_by':    artist.claimed_by,
                    }
                )

            # 2. Copy any richer profile fields the promoter is missing
            for field in ('bio', 'website', 'instagram', 'soundcloud',
                          'bandcamp', 'mixcloud', 'youtube', 'spotify',
                          'mastodon', 'bluesky'):
                if not getattr(promoter, field, '') and getattr(artist, field, ''):
                    setattr(promoter, field, getattr(artist, field))
            if not promoter.bio and artist.auto_bio:
                promoter.bio = artist.auto_bio
            promoter.save()

            # 3. Migrate all artist event links → promoter
            for ev in artist.events.all():
                ev.promoters.add(promoter)

            # 4. Remove from recurring-event resident lists
            for rec in artist.recurring_events.all():
                rec.residents.remove(artist)
            for feed in artist.resident_feeds.all():
                feed.residents.remove(artist)

            # 5. Delete the artist stub
            name = artist.name
            artist.delete()
            retired += 1

        noun = 'artist' if retired == 1 else 'artists'
        self.message_user(
            request,
            f'{retired} {noun} retired → crew profile. {skipped} skipped (not stubs).',
            messages.SUCCESS,
        )
    retire_as_crew.short_description = '🪦 Retire selected as Crew (migrate events + delete Artist)'


def _promoter_score(p):
    """Rank promoter profiles for canonical merge: verified > most events > claimed > older pk."""
    return (
        p.is_verified * 1000 +
        p.events.count() * 10 +
        bool(p.claimed_by) * 5 +
        bool(p.bio) * 2 +
        (-p.pk)  # lower pk = created earlier = more canonical
    )


def merge_promoters(modeladmin, request, queryset):
    """Merge selected PromoterProfiles into the most canonical one."""
    promoters = list(queryset)
    if len(promoters) < 2:
        modeladmin.message_user(request, 'Select at least 2 promoters/crews to merge.', messages.WARNING)
        return

    winner = max(promoters, key=_promoter_score)
    losers = [p for p in promoters if p.pk != winner.pk]

    for loser in losers:
        # Events M2M
        for event in loser.events.all():
            event.promoters.remove(loser)
            event.promoters.add(winner)
        # Artist linked_promoter FK
        from events.models import Artist
        Artist.objects.filter(linked_promoter=loser).update(linked_promoter=winner)
        # VenueFeed FK
        from events.models import VenueFeed
        VenueFeed.objects.filter(promoter=loser).update(promoter=winner)
        # Carry over missing profile fields
        for field in ('bio', 'photo', 'website', 'instagram', 'soundcloud',
                      'bandcamp', 'mixcloud', 'youtube', 'spotify', 'mastodon',
                      'bluesky', 'tiktok', 'drive_folder_url', 'admin_email',
                      'name_variants'):
            if not getattr(winner, field, '') and getattr(loser, field, ''):
                setattr(winner, field, getattr(loser, field))
        if not winner.claimed_by and loser.claimed_by:
            winner.claimed_by = loser.claimed_by
        if not winner.is_verified and loser.is_verified:
            winner.is_verified = True
        loser.delete()

    winner.save()
    modeladmin.message_user(
        request,
        f'Merged {len(losers)} duplicate(s) into "{winner.name}" (pk={winner.pk}).',
        messages.SUCCESS,
    )

merge_promoters.short_description = 'Merge selected crews into the most canonical one'


class PromoterProfileAdminForm(forms.ModelForm):
    promoter_type = forms.MultipleChoiceField(
        choices=PromoterProfile.TYPE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=True,
    )

    class Meta:
        model = PromoterProfile
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = kwargs.get('instance')
        if instance:
            self.initial['promoter_type'] = instance.types

    def clean_promoter_type(self):
        return self.cleaned_data['promoter_type']


@admin.register(PromoterProfile)
class PromoterProfileAdmin(admin.ModelAdmin):
    form = PromoterProfileAdminForm
    search_fields = ['name', 'slug', 'admin_email']
    ordering = ['name']
    list_display = ['name', 'slug', 'name_variants', 'admin_email', 'is_verified', 'is_public', 'has_drive', 'claimed_by']
    list_editable = ['is_verified', 'is_public']
    list_filter = ['is_verified', 'is_public']
    raw_id_fields = ['claimed_by']
    filter_horizontal = ['genres']
    actions = [merge_promoters, 'send_claim_instructions', 'convert_to_artist']

    def has_drive(self, obj):
        return bool(obj.drive_folder_url)
    has_drive.boolean = True
    has_drive.short_description = 'Drive'

    change_form_template = 'admin/events/promoterprofile/change_form.html'

    def get_urls(self):
        from django.urls import path as _path
        urls = super().get_urls()
        return [
            _path('<int:pk>/send-claim-email/',
                  self.admin_site.admin_view(self._send_claim_email_view),
                  name='events_promoterprofile_send_claim_email'),
        ] + urls

    def _send_claim_email_view(self, request, pk):
        from django.shortcuts import redirect, get_object_or_404
        from django.urls import reverse
        obj = get_object_or_404(PromoterProfile, pk=pk)
        if not obj.admin_email:
            self.message_user(request, 'No admin email set on this profile.', level='error')
        else:
            self._do_send_claim_email(request, obj, 'promoters')
        return redirect(reverse('admin:events_promoterprofile_change', args=[pk]))

    @staticmethod
    def _do_send_claim_email(request, obj, url_segment):
        from django.core.mail import send_mail
        profile_url = f'https://communityplaylist.com/{url_segment}/{obj.slug}/'
        body = (
            'Hey!\n\n'
            f'Your profile on Community Playlist is live:\n{profile_url}\n\n'
            'To take ownership — manage events, sync your record shop, and keep your info '
            'up to date — create a free account and then claim your profile:\n\n'
            '1. Register (or log in): https://communityplaylist.com/register/\n'
            f'2. Visit your profile:   {profile_url}\n'
            '3. Click "Claim this profile" and you\'re in.\n\n'
            'Any questions? Reply to this email.\n\n'
            '-- Community Playlist\n'
            'https://communityplaylist.com'
        )
        try:
            send_mail(
                subject=f'Claim your Community Playlist profile — {obj.name}',
                message=body,
                from_email='Community Playlist <noreply@communityplaylist.com>',
                recipient_list=[obj.admin_email],
                fail_silently=False,
            )
            from django.contrib import messages
            messages.success(request, f'Claim email sent to {obj.admin_email}.')
        except Exception as e:
            from django.contrib import messages
            messages.error(request, f'Mail error: {e}')

    def send_claim_instructions(self, request, queryset):
        sent, skipped = 0, 0
        for promoter in queryset:
            if not promoter.admin_email or promoter.claimed_by:
                skipped += 1
                continue
            self._do_send_claim_email(request, promoter, 'promoters')
            sent += 1
        if sent:
            self.message_user(request, f'Claim instructions sent to {sent} profile(s).')
        if skipped:
            self.message_user(request, f'{skipped} skipped (no email or already claimed).', level='warning')
    send_claim_instructions.short_description = 'Send claim instructions email'

    def convert_to_artist(self, request, queryset):
        """Create an Artist profile for each selected crew and link them."""
        from django.utils.text import slugify as _slugify
        created = already = 0
        for promoter in queryset:
            if promoter.linked_artists.exists():
                already += 1
                continue
            slug = _slugify(promoter.name)
            artist, new = Artist.objects.get_or_create(
                slug=slug,
                defaults={
                    'name':       promoter.name,
                    'bio':        promoter.bio,
                    'website':    promoter.website,
                    'instagram':  promoter.instagram,
                    'soundcloud': promoter.soundcloud,
                    'spotify':    promoter.spotify,
                    'claimed_by': promoter.claimed_by,
                    'is_stub':    not bool(promoter.bio),
                }
            )
            artist.linked_promoter = promoter
            artist.save(update_fields=['linked_promoter'])
            # Pull all promoter events into the artist M2M
            for ev in promoter.events.all():
                ev.artists.add(artist)
            created += 1
        self.message_user(request, f'{created} artist profile(s) created and linked, {already} already linked.')
    convert_to_artist.short_description = '🎤 Convert selected crews → Artist profile'


class HasPreviewFilter(admin.SimpleListFilter):
    title = 'preview video'
    parameter_name = 'has_preview'

    def lookups(self, request, model_admin):
        return [('yes', 'Has preview'), ('no', 'No preview')]

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.exclude(preview_url='')
        if self.value() == 'no':
            return queryset.filter(preview_url='')


@admin.register(RecordListing)
class RecordListingAdmin(admin.ModelAdmin):
    list_display = ['artist', 'title', 'label', 'year', 'format', 'condition',
                    'price_sol', 'is_available', 'promoter', 'has_preview', 'preview_link']
    list_filter = ['is_available', 'format', 'condition', HasPreviewFilter]
    list_editable = ['is_available']
    search_fields = ['artist', 'title', 'label', 'promoter__name']
    raw_id_fields = ['promoter']
    ordering = ['promoter', 'row_index']

    def has_preview(self, obj):
        return bool(obj.preview_url)
    has_preview.boolean = True
    has_preview.short_description = '▶'

    def preview_link(self, obj):
        if obj.preview_url:
            return format_html('<a href="{}" target="_blank" rel="noopener">YouTube ↗</a>', obj.preview_url)
        return '—'
    preview_link.short_description = 'Preview URL'


@admin.register(RecordReservation)
class RecordReservationAdmin(admin.ModelAdmin):
    list_display = ['listing', 'buyer_name', 'buyer_email', 'status', 'created_at']
    list_filter = ['status']
    list_editable = ['status']
    search_fields = ['buyer_name', 'buyer_email', 'buyer_contact', 'listing__artist', 'listing__title']
    raw_id_fields = ['listing']
    readonly_fields = ['created_at']
    ordering = ['-created_at']


@admin.register(PlaylistTrack)
class PlaylistTrackAdmin(admin.ModelAdmin):
    list_display = ['title', 'artist_name', 'genre', 'source_display', 'recorded_at', 'last_synced']
    list_filter = ['genre']
    search_fields = ['title', 'artist_name', 'drive_file_id']
    raw_id_fields = ['artist', 'promoter', 'venue', 'genre']

    def source_display(self, obj):
        return obj.source_label
    source_display.short_description = 'Source'


@admin.register(VideoTrack)
class VideoTrackAdmin(admin.ModelAdmin):
    list_display  = ['artist_name_display', 'title_truncated', 'channel_title',
                     'source_type', 'published_at', 'play_count', 'is_active']
    list_filter   = ['is_active']
    search_fields = ['title', 'artist_name_display', 'channel_title', 'youtube_video_id']
    raw_id_fields = ['artist', 'promoter', 'venue']
    list_editable = ['is_active']
    ordering      = ['-published_at']
    readonly_fields = ['youtube_video_id', 'youtube_channel_id', 'play_count',
                       'published_at', 'last_synced', 'video_preview']

    def title_truncated(self, obj):
        return obj.title[:60] + ('…' if len(obj.title) > 60 else '')
    title_truncated.short_description = 'Title'

    def source_type(self, obj):
        if obj.artist_id:   return f'Artist: {obj.artist}'
        if obj.promoter_id: return f'Promoter: {obj.promoter}'
        if obj.venue_id:    return f'Venue: {obj.venue}'
        return '—'
    source_type.short_description = 'Source'

    def video_preview(self, obj):
        return format_html(
            '<a href="https://youtube.com/watch?v={}" target="_blank">'
            '<img src="{}" style="max-width:200px;border-radius:4px"></a>',
            obj.youtube_video_id, obj.thumbnail_url or ''
        )
    video_preview.short_description = 'Preview'


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
    actions = ['verify_venues', 'autofill_from_website', 'close_venue', 'queue_venue_geocoding']

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

    def queue_venue_geocoding(self, request, queryset):
        """Queue geocode_venue tasks for venues that have an address but no coordinates."""
        from events.models import WorkerTask
        to_geocode = queryset.filter(address__gt='').filter(latitude__isnull=True)
        tasks = [
            WorkerTask(task_type='geocode_venue', payload={'venue_id': v.id, 'address': v.address})
            for v in to_geocode
        ]
        if tasks:
            WorkerTask.objects.bulk_create(tasks, ignore_conflicts=True)
        already = queryset.count() - len(tasks)
        parts = []
        if tasks:    parts.append(f'{len(tasks)} venue{"s" if len(tasks) != 1 else ""} queued for geocoding')
        if already:  parts.append(f'{already} already had coordinates')
        self.message_user(
            request,
            ' · '.join(parts) if parts else 'No venues with addresses found to geocode.',
            messages.SUCCESS if tasks else messages.WARNING,
        )
    queue_venue_geocoding.short_description = 'Queue geocoding for venues missing coordinates'

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


def _event_score(ev):
    """Higher = more canonical. Used to pick the winner when deduplicating events."""
    return (
        (ev.status == 'approved') * 1000 +
        bool(ev.photo) * 100 +
        bool(ev.description and len(ev.description) > 50) * 50 +
        bool(ev.website) * 20 +
        bool(ev.neighborhood) * 10 +
        bool(ev.artists.exists()) * 10 -
        ev.pk  # tie-break: prefer older record
    )


def merge_events(modeladmin, request, queryset):
    """Merge selected Event records into the most canonical one (most data, approved, oldest)."""
    events = list(queryset.prefetch_related('artists', 'genres', 'photos'))
    if len(events) < 2:
        modeladmin.message_user(request, 'Select at least 2 events to merge.', messages.WARNING)
        return

    winner = max(events, key=_event_score)
    losers = [e for e in events if e.pk != winner.pk]

    for loser in losers:
        # Move M2M links to winner
        for artist in loser.artists.all():
            winner.artists.add(artist)
        for genre in loser.genres.all():
            winner.genres.add(genre)
        # Carry over fields winner is missing
        for field in ('description', 'website', 'photo', 'neighborhood', 'end_date',
                      'ticket_url', 'is_free', 'category'):
            if not getattr(winner, field, None) and getattr(loser, field, None):
                setattr(winner, field, getattr(loser, field))
        if winner.status != 'approved' and loser.status == 'approved':
            winner.status = 'approved'
        loser.delete()

    winner.save()
    modeladmin.message_user(
        request,
        f'Merged {len(losers)} duplicate(s) into "{winner.title}" (pk={winner.pk}).',
        messages.SUCCESS,
    )

merge_events.short_description = 'Merge selected events into the most canonical one'


def dedup_by_title_date(modeladmin, request, queryset):
    """Auto-delete exact duplicates (same title + same start date) in the selection, keeping the best."""
    from itertools import groupby
    from django.utils.text import slugify as _slugify

    events = list(queryset.order_by('title', 'start_date'))
    removed = 0

    # Group by normalised title + date
    def key(e):
        return (_slugify(e.title), e.start_date.date() if e.start_date else None)

    seen = {}
    for ev in events:
        k = key(ev)
        seen.setdefault(k, []).append(ev)

    for k, group in seen.items():
        if len(group) < 2:
            continue
        winner = max(group, key=_event_score)
        for loser in group:
            if loser.pk == winner.pk:
                continue
            for artist in loser.artists.all():
                winner.artists.add(artist)
            for genre in loser.genres.all():
                winner.genres.add(genre)
            loser.delete()
            removed += 1
        winner.save()

    modeladmin.message_user(
        request,
        f'Removed {removed} duplicate event(s). Winners kept for each title+date group.',
        messages.SUCCESS if removed else messages.WARNING,
    )

dedup_by_title_date.short_description = 'Auto-remove duplicates (same title + date, keep best)'


def fill_address_and_geocode(modeladmin, request, queryset):
    """Redirect to the geocode-progress page for live streaming output."""
    ids = ','.join(str(e.pk) for e in queryset)
    return HttpResponseRedirect(f'geocode-progress/?ids={ids}')

fill_address_and_geocode.short_description = 'Geocode & assign neighborhood (live progress)'


def link_twitch_location_artists(modeladmin, request, queryset):
    """
    For events whose location is a twitch.tv URL, extract the handle,
    find or create an Artist with that Twitch handle, and link them to the event.
    """
    import re
    from django.utils.text import slugify as _slugify

    TWITCH_RE = re.compile(r'https?://(?:www\.)?twitch\.tv/([A-Za-z0-9_]+)/?', re.I)

    created = linked = already = skipped = 0

    for ev in queryset:
        m = TWITCH_RE.match((ev.location or '').strip())
        if not m:
            skipped += 1
            continue
        handle = m.group(1).lower()

        artist = Artist.objects.filter(twitch__iexact=handle).first()
        if not artist:
            # Humanise handle → name (beersandsbeatspdx → Beersandsbeatspdx)
            name = handle.replace('_', ' ').title()
            base_slug = _slugify(name) or handle
            slug = base_slug
            n = 1
            while Artist.objects.filter(slug=slug).exists():
                slug = f'{base_slug}-{n}'; n += 1
            artist = Artist.objects.create(name=name, slug=slug, twitch=handle)
            created += 1

        if ev.artists.filter(pk=artist.pk).exists():
            already += 1
        else:
            ev.artists.add(artist)
            linked += 1

    parts = []
    if created:  parts.append(f'{created} artist stub{"s" if created != 1 else ""} created')
    if linked:   parts.append(f'{linked} event{"s" if linked != 1 else ""} linked')
    if already:  parts.append(f'{already} already linked')
    if skipped:  parts.append(f'{skipped} skipped (no Twitch URL)')
    modeladmin.message_user(
        request,
        ' · '.join(parts) if parts else 'No Twitch location URLs found in selection.',
        messages.SUCCESS if (created or linked) else messages.WARNING,
    )

link_twitch_location_artists.short_description = 'Auto-create/link artists from Twitch location URLs'


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
    actions = [merge_events, dedup_by_title_date, fill_address_and_geocode, link_twitch_location_artists]

    def get_urls(self):
        from django.urls import path as _path
        urls = super().get_urls()
        custom = [
            _path('geocode-progress/', self.admin_site.admin_view(self._geocode_progress_page), name='event_geocode_progress'),
            _path('geocode-stream/',   self.admin_site.admin_view(self._geocode_stream),         name='event_geocode_stream'),
        ]
        return custom + urls

    def _geocode_progress_page(self, request):
        ids = request.GET.get('ids', '')
        id_count = len([i for i in ids.split(',') if i.strip().isdigit()])
        ctx = {**self.admin_site.each_context(request), 'ids': ids, 'id_count': id_count, 'title': 'Geocoding events…'}
        return TemplateResponse(request, 'admin/events/geocode_progress.html', ctx)

    def _geocode_stream(self, request):
        ids_raw = request.GET.get('ids', '')
        try:
            ids = [int(i) for i in ids_raw.split(',') if i.strip().isdigit()]
        except ValueError:
            ids = []

        auto_approve = request.GET.get('auto_approve') == '1'

        def _stream(ids, auto_approve):
            import re, unicodedata, time as _time
            from events.models import Venue
            from events.geocode import geocode_location, reverse_geocode_neighborhood

            def _sse(data):
                msg = f'data: {data}\n\n'
                return msg + ':' + ' ' * max(0, 1024 - len(msg)) + '\n\n'

            def _fold(s):
                return unicodedata.normalize('NFD', s.lower()).encode('ascii', 'ignore').decode()

            venues = list(Venue.objects.filter(active=True).only('name', 'address', 'latitude', 'longitude'))

            def _match_venue(location):
                if not location or location.startswith(('http://', 'https://')):
                    return None
                loc_f = _fold(location)
                for v in venues:
                    name_f = _fold(v.name.strip())
                    addr_f = _fold(v.address.strip()) if v.address else ''
                    if name_f and len(name_f) > 4 and name_f in loc_f:
                        return v
                    if addr_f and len(addr_f) > 8 and addr_f[:40] in loc_f:
                        return v
                return None

            def _save_geocoded(ev, extra_fields):
                """Save geocode fields and optionally approve the event."""
                fields = list(extra_fields)
                if auto_approve and ev.status != 'approved':
                    ev.status = 'approved'
                    fields.append('status')
                ev.save(update_fields=fields)

            def _tag(hood):
                return f'[{hood}] ' if hood else ''

            events = list(Event.objects.filter(pk__in=ids))
            total = len(events)
            yield _sse(f'TOTAL {total}')

            done = coord_copied = geocoded = failed = already = approved = 0
            for ev in events:
                done += 1
                if ev.latitude:
                    already += 1
                    # Still approve if requested and not yet approved
                    if auto_approve and ev.status != 'approved':
                        ev.status = 'approved'
                        ev.save(update_fields=['status'])
                        approved += 1
                        yield _sse(f'SKIP [{done}/{total}] {ev.title[:50]} — already geocoded → ✓ approved')
                    else:
                        yield _sse(f'SKIP [{done}/{total}] {ev.title[:50]} — already geocoded')
                    continue

                # When location is blank/URL, try submitted_by as a venue name hint
                if not ev.location or ev.location.startswith(('http', 'www')):
                    submitter = (ev.submitted_by or '').strip()
                    venue = _match_venue(submitter)
                    if venue and venue.latitude and venue.address:
                        ev.location = venue.address
                        ev.latitude, ev.longitude = venue.latitude, venue.longitude
                        hood = reverse_geocode_neighborhood(venue.latitude, venue.longitude)
                        ev.neighborhood = hood
                        _save_geocoded(ev, ['location', 'latitude', 'longitude', 'neighborhood', 'status'])
                        coord_copied += 1
                        if auto_approve: approved += 1
                        yield _sse(f'VENUE [{done}/{total}] {_tag(hood)}{ev.title[:45]} → {venue.name} via submitter{" ✓" if auto_approve else ""}')
                        _time.sleep(0.5)
                    elif submitter and not submitter.startswith(('http', 'www')):
                        try:
                            query = f'{submitter}, Portland, OR'
                            lat, lng = geocode_location(query)
                            if lat and lng:
                                hood = reverse_geocode_neighborhood(lat, lng)
                                ev.location = query
                                ev.latitude, ev.longitude = lat, lng
                                ev.neighborhood = hood
                                _save_geocoded(ev, ['location', 'latitude', 'longitude', 'neighborhood', 'status'])
                                geocoded += 1
                                if auto_approve: approved += 1
                                yield _sse(f'OK [{done}/{total}] {_tag(hood)}{ev.title[:45]} → "{submitter}"{" ✓" if auto_approve else ""}')
                            else:
                                failed += 1
                                yield _sse(f'FAIL [{done}/{total}] {ev.title[:50]} — "{submitter}" not found')
                        except Exception as exc:
                            failed += 1
                            yield _sse(f'ERR [{done}/{total}] {ev.title[:50]} — {exc}')
                        _time.sleep(1.1)
                    else:
                        failed += 1
                        yield _sse(f'FAIL [{done}/{total}] {ev.title[:50]} — no location or submitter')
                    continue

                venue = _match_venue(ev.location)
                if venue and venue.latitude:
                    fields = ['latitude', 'longitude', 'neighborhood', 'status']
                    ev.latitude, ev.longitude = venue.latitude, venue.longitude
                    if venue.address and not re.search(r'\d+\s+\w', ev.location):
                        ev.location = venue.address
                        fields.append('location')
                    hood = reverse_geocode_neighborhood(venue.latitude, venue.longitude)
                    ev.neighborhood = hood
                    _save_geocoded(ev, fields)
                    coord_copied += 1
                    if auto_approve: approved += 1
                    yield _sse(f'VENUE [{done}/{total}] {_tag(hood)}{ev.title[:45]} → {venue.name}{" ✓" if auto_approve else ""}')
                    _time.sleep(0.5)
                    continue

                try:
                    lat, lng = geocode_location(ev.location)
                    if lat and lng:
                        ev.latitude, ev.longitude = lat, lng
                        hood = reverse_geocode_neighborhood(lat, lng)
                        ev.neighborhood = hood
                        _save_geocoded(ev, ['latitude', 'longitude', 'neighborhood', 'status'])
                        geocoded += 1
                        if auto_approve: approved += 1
                        yield _sse(f'OK [{done}/{total}] {_tag(hood)}{ev.title[:45]}{" ✓" if auto_approve else ""}')
                    else:
                        failed += 1
                        yield _sse(f'FAIL [{done}/{total}] {ev.title[:50]} — no result for: {ev.location[:50]}')
                except Exception as exc:
                    failed += 1
                    yield _sse(f'ERR [{done}/{total}] {ev.title[:50]} — {exc}')
                _time.sleep(1.1)

            yield _sse(f'DONE venue={coord_copied} geocoded={geocoded} approved={approved} failed={failed} skipped={already}')

        resp = StreamingHttpResponse(_stream(ids, auto_approve), content_type='text/event-stream')
        resp['Cache-Control'] = 'no-cache'
        resp['X-Accel-Buffering'] = 'no'
        return resp

    def save_model(self, request, obj, form, change):
        old_status = None
        if obj.pk:
            old_status = Event.objects.get(pk=obj.pk).status
        super().save_model(request, obj, form, change)
        if obj.status == 'approved' and old_status != 'approved':
            from board.social import post_event_discord, create_discord_scheduled_event
            post_event_discord(obj)           # rich embed → #events text/forum channel
            create_discord_scheduled_event(obj)  # native event → Discord Events tab
            from events.bluesky import post_event_to_bluesky
            post_event_to_bluesky(obj)


@admin.register(EventPhoto)
class EventPhotoAdmin(admin.ModelAdmin):
    list_display = ['event', 'photo_type', 'submitted_by', 'approved', 'created_at']
    list_filter = ['approved', 'photo_type']
    list_editable = ['approved']


@admin.register(VenueFeed)
class VenueFeedAdmin(admin.ModelAdmin):
    list_display = ['name', 'promoter', 'source_type', 'active', 'auto_approve', 'default_category', 'genre_list', 'last_synced', 'health_status']
    list_filter = ['source_type', 'active', 'auto_approve', 'default_category']
    list_editable = ['active', 'auto_approve']
    search_fields = ['name', 'url', 'notes']
    ordering = ['name']
    readonly_fields = ['last_synced', 'last_error', 'created_at']
    fieldsets = (
        (None, {'fields': ('name', 'website', 'source_type', 'url', 'promoter')}),
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
        'log':      'logs/cp_import_feeds.log',
        'schedule': 'Mon + Thu  6:00 AM',
    },
    {
        'name':     'Import venue / PDX net feeds',
        'command':  'import_venue_feeds',
        'log':      'logs/cp_import_venues.log',
        'schedule': 'Mon + Thu  7:00 AM',
    },
    {
        'name':     'Generate recurring event instances',
        'command':  'generate_recurring_events',
        'log':      'logs/cp_recurring.log',
        'schedule': 'Daily  6:05 AM',
    },
    {
        'name':     'Geocode events (20/night)',
        'command':  'geocode_events',
        'log':      'logs/cp_geocode.log',
        'schedule': 'Daily  2:00 AM',
    },
    {
        'name':     'Fetch event images (og:image)',
        'command':  'fetch_event_images',
        'log':      'logs/cp_fetch_images.log',
        'schedule': 'Daily  3:00 AM',
    },
    {
        'name':     'Daily Discord digest',
        'command':  'daily_digest',
        'log':      'logs/cp_daily_digest.log',
        'schedule': 'Daily  9:00 AM',
    },
    {
        'name':     'Recheck inactive venue feeds',
        'command':  'recheck_venue_feeds',
        'log':      'logs/cp_recheck_feeds.log',
        'schedule': '1st of month  8:00 AM',
    },
    {
        'name':     'Discover new PDX feeds',
        'command':  'discover_pdx_feeds',
        'log':      'logs/cp_discover_feeds.log',
        'schedule': '1st of month  8:05 AM',
    },
    {
        'name':     'Check live streams (YouTube + Twitch)',
        'command':  'check_live_streams',
        'log':      'logs/cp_live_streams.log',
        'schedule': 'Every 10 min',
    },
    {
        'name':     'Dedup events (cross-feed merge)',
        'command':  'dedup_events',
        'log':      'logs/cp_dedup_events.log',
        'schedule': 'Mon + Thu  7:30 AM (after import)',
    },
    {
        'name':     'Enrich from Instagram (bio + links + photo)',
        'command':  'enrich_instagram',
        'log':      'logs/cp_enrich_instagram.log',
        'schedule': 'Weekly  Sunday  4:00 AM',
    },
]


def _parse_log(path, tail_lines=25):
    """Read a log file. Returns (mtime_dt, last_lines, has_error)."""
    if not os.path.isabs(path):
        from django.conf import settings as _s
        path = os.path.join(str(_s.BASE_DIR), path)
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
            return format_html('<a href="/artists/{}/" target="_blank">{}</a>', target.slug or target.pk, target.name)
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
    'check_live_streams':        'events',
    'sweep_spam_topics':         'board',
}


@admin.register(WorkerTask)
class WorkerTaskAdmin(admin.ModelAdmin):
    list_display  = ['task_type', 'status', 'payload_summary', 'created_at', 'completed_at', 'error_msg']
    list_filter   = ['task_type', 'status']
    ordering      = ['-created_at']
    readonly_fields = ['task_type', 'payload', 'status', 'result', 'error_msg', 'created_at', 'completed_at']
    actions       = ['retry_errored', 'clear_done']

    def payload_summary(self, obj):
        if 'address' in obj.payload:
            return obj.payload['address'][:60]
        return str(obj.payload)[:60]
    payload_summary.short_description = 'Address / Payload'

    def has_add_permission(self, request):
        return False

    def retry_errored(self, request, queryset):
        n = queryset.filter(status='error').update(status='queued', error_msg='')
        self.message_user(request, f'{n} task(s) re-queued.')
    retry_errored.short_description = 'Re-queue errored tasks'

    def clear_done(self, request, queryset):
        n, _ = queryset.filter(status='done').delete()
        self.message_user(request, f'{n} completed task(s) deleted.')
    clear_done.short_description = 'Delete completed tasks'


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

        import sys
        from django.conf import settings as _settings
        base_dir = str(_settings.BASE_DIR)
        rel_log  = _COMMAND_MAP[command].get('log', f'logs/cp_{command}.log')
        # Make log path absolute relative to project root
        log_path = rel_log if os.path.isabs(rel_log) else os.path.join(base_dir, rel_log)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

        manage_py = os.path.join(base_dir, 'manage.py')
        python    = sys.executable  # same interpreter that's running gunicorn

        try:
            log_fh = open(log_path, 'a')
            subprocess.Popen(
                [python, manage_py, command],
                cwd=base_dir,
                env={**os.environ, 'DJANGO_SETTINGS_MODULE': 'communityplaylist.settings'},
                stdout=log_fh,
                stderr=log_fh,
                start_new_session=True,
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

# ── Shelter / Resource Admin ─────────────────────────────────────────────────

PDX_SHELTER_SEED = [
    # Emergency / Overnight
    dict(name='JOIN PDX – Street Outreach',shelter_type='emergency',accepts='all',
         address='4126 NE Sandy Blvd, Portland, OR 97212',phone='503-232-0007',
         website='https://joinpdx.org',hours='Mon–Fri 9am–5pm (outreach 24/7)',
         available_cold=True,available_hot=False,available_smoke=False,
         notes='Street outreach, shelter navigation, transitional housing referrals.'),
    dict(name="Transition Projects – Jean's Place",shelter_type='day',accepts='all',
         address='665 NW Hoyt St, Portland, OR 97209',phone='503-280-4712',
         website='https://www.tprojects.org',hours='Daily 7am–10pm',
         available_cold=True,available_hot=True,available_smoke=True,
         notes='Day shelter, showers, laundry, meals. No intake barriers.'),
    dict(name='Central City Concern – Old Town Recovery Center',shelter_type='sobering',accepts='all',
         address='444 NW 5th Ave, Portland, OR 97209',phone='503-294-1681',
         website='https://www.centralcityconcern.org',hours='24/7',
         available_cold=True,available_hot=False,available_smoke=False,
         notes='Sobering center and detox. Medical staff on-site. No appointment needed.'),
    dict(name='Blanchet House',shelter_type='day',accepts='all',
         address='310 NW Glisan St, Portland, OR 97209',phone='503-241-4340',
         website='https://www.blanchethouse.org',hours='Daily 6am–7pm',
         available_cold=True,available_hot=True,available_smoke=True,
         notes='Free meals three times daily. No ID required. Indoor dining room.'),
    dict(name="Transition Projects – Clark Center (Men's)",shelter_type='overnight',accepts='men',
         address='4611 SE Belmont St, Portland, OR 97215',phone='503-294-7400',
         website='https://www.tprojects.org',hours='Nightly — call for intake hours',
         available_cold=True,available_hot=False,available_smoke=False,
         notes="Men's overnight shelter. Call ahead for current bed availability."),
    dict(name='NAOMI – Safe House',shelter_type='womens',accepts='women',
         address='Confidential – call for location',phone='503-295-3906',
         website='https://www.naomipnw.org',hours='24/7',
         available_cold=True,available_hot=False,available_smoke=False,
         notes='Domestic violence safe house for women and children. Location confidential — call first.'),
    dict(name='Outside In – Youth Shelter',shelter_type='youth',accepts='youth',
         address='1132 SW 13th Ave, Portland, OR 97205',phone='503-223-4121',
         website='https://outsidein.org',hours='Mon–Fri 9am–5pm drop-in; overnight for enrolled youth',
         available_cold=True,available_hot=True,available_smoke=True,
         notes='Youth ages 13–25. LGBTQ+ affirming. Drop-in services, health clinic, meals.'),
    dict(name='Youth Villages – New Directions',shelter_type='youth',accepts='youth',
         address='Phone intake only',phone='503-872-0012',
         website='https://youthvillages.org',hours='24/7 crisis line',
         available_cold=True,available_hot=False,available_smoke=False,
         notes='Youth crisis intervention, shelter placement navigation.'),
    dict(name='Portland Rescue Mission',shelter_type='overnight',accepts='all',
         address='111 W Burnside St, Portland, OR 97209',phone='503-227-0421',
         website='https://www.portlandrescuemission.org',hours='Nightly — call for hours',
         available_cold=True,available_hot=False,available_smoke=False,
         notes='Meals and overnight shelter. ID not required for emergency services.'),
    # Warming / Cooling
    dict(name='Multnomah County – Extreme Heat Emergency Shelter',shelter_type='cooling',accepts='all',
         address='Varies — check multco.us on heat advisory days',phone='211',
         website='https://www.multco.us/emergency-management',hours='During heat advisories only',
         available_cold=False,available_hot=True,available_smoke=False,
         notes='County opens cooling centers at libraries and community centers during extreme heat. Call 211 for locations.'),
    dict(name='Multnomah County – Warming Center Network',shelter_type='warming',accepts='all',
         address='Varies — activated during freeze alerts',phone='211',
         website='https://www.multco.us/emergency-management',hours='During freeze/cold weather alerts',
         available_cold=True,available_hot=False,available_smoke=False,
         notes='County activates network of warming centers when temps drop below freezing. Call 211 for current locations.'),
    # Hygiene
    dict(name='JOIN PDX – Resource Navigation',shelter_type='hygiene',accepts='all',
         address='4126 NE Sandy Blvd, Portland, OR 97212',phone='503-232-0007',
         website='https://joinpdx.org',hours='Mon–Fri 9am–5pm',
         available_cold=False,available_hot=False,available_smoke=False,
         notes='Showers, hygiene kits, laundry referrals, storage lockers, mail service.'),
    dict(name='SnowCap Community Charities',shelter_type='hygiene',accepts='all',
         address='12655 NE Glisan St, Portland, OR 97230',phone='503-262-8706',
         website='https://www.snowcap.org',hours='Mon–Fri 9am–4pm',
         available_cold=False,available_hot=False,available_smoke=False,
         notes='Food pantry, hygiene supplies, clothing. East Portland focus.'),
    # Veterans
    dict(name='VA Portland – Homeless Veterans Services',shelter_type='veteran',accepts='veteran',
         address='3710 SW US Veterans Hospital Rd, Portland, OR 97239',phone='503-220-8262',
         website='https://www.portland.va.gov',hours='Mon–Fri 8am–4:30pm',
         available_cold=True,available_hot=False,available_smoke=False,
         notes='HUD-VASH vouchers, transitional housing, SSVF rapid re-housing. DD-214 required.'),
    # Hotlines
    dict(name='211info – Oregon/SW Washington',shelter_type='hotline',accepts='all',
         address='Phone / text only',phone='211',
         website='https://www.211info.org',hours='24/7',
         available_cold=True,available_hot=True,available_smoke=True,
         notes='Call or text 211 for shelter locations, food, utilities, mental health, and any social service need. Free, confidential, multilingual.'),
    dict(name='Lines for Life – Crisis Line',shelter_type='hotline',accepts='all',
         address='Phone only',phone='800-273-8255',
         website='https://www.linesforlife.org',hours='24/7',
         available_cold=True,available_hot=False,available_smoke=False,
         notes='Mental health crisis line. Also text HOME to 741741. Suicide prevention and substance use crisis support.'),
    dict(name='Oregon 211 – Heat/Cold Emergency Locations',shelter_type='hotline',accepts='all',
         address='Phone only',phone='211',
         website='https://www.211info.org',hours='24/7 during weather emergencies',
         available_cold=True,available_hot=True,available_smoke=True,
         notes='Dedicated line for real-time warming/cooling center locations during declared weather emergencies.'),
]

def _seed_shelters():
    from events.models import Shelter
    from django.utils.text import slugify
    created = 0
    for data in PDX_SHELTER_SEED:
        slug = slugify(data['name'])
        if not Shelter.objects.filter(slug=slug).exists():
            s = Shelter(**data)
            s.save()
            created += 1
    return created


@admin.register(Shelter)
class ShelterAdmin(admin.ModelAdmin):
    list_display  = ['name', 'shelter_type', 'accepts', 'pets_ok', 'phone',
                     'available_cold', 'available_hot', 'available_smoke', 'active']
    list_filter   = ['shelter_type', 'accepts', 'available_cold', 'available_hot',
                     'available_smoke', 'active', 'pets_ok']
    search_fields = ['name', 'address', 'neighborhood', 'notes']
    ordering      = ['shelter_type', 'name']
    readonly_fields = ['created_at', 'updated_at', 'slug']
    actions       = ['activate', 'deactivate', 'seed_pdx_shelters']

    fieldsets = [
        ('Identity', {'fields': ['name', 'slug', 'shelter_type', 'accepts', 'pets_ok', 'active']}),
        ('Location', {'fields': ['address', 'neighborhood', 'latitude', 'longitude']}),
        ('Contact & Hours', {'fields': ['phone', 'website', 'hours', 'capacity']}),
        ('Weather Flags', {
            'description': 'Check the conditions under which this shelter should be surfaced automatically.',
            'fields': ['available_cold', 'available_hot', 'available_smoke'],
        }),
        ('Notes', {'fields': ['notes']}),
        ('Meta', {'fields': ['created_at', 'updated_at'], 'classes': ['collapse']}),
    ]

    def activate(self, request, queryset):
        queryset.update(active=True)
        self.message_user(request, f'{queryset.count()} shelter(s) activated.')
    activate.short_description = '✅ Mark selected as active'

    def deactivate(self, request, queryset):
        queryset.update(active=False)
        self.message_user(request, f'{queryset.count()} shelter(s) deactivated.')
    deactivate.short_description = '🚫 Mark selected as inactive'

    def seed_pdx_shelters(self, request, queryset):
        n = _seed_shelters()
        self.message_user(request, f'Seeded {n} new PDX shelters (skipped existing).')
    seed_pdx_shelters.short_description = '🌱 Seed PDX shelter list (skips duplicates)'


class InstagramPostInline(admin.TabularInline):
    model = InstagramPost
    extra = 0
    readonly_fields = ['ig_post_id', 'shortcode', 'caption_preview', 'is_video', 'posted_at', 'fetched_at', 'permalink_link']
    fields = ['ig_post_id', 'shortcode', 'caption_preview', 'is_video', 'posted_at', 'permalink_link']
    ordering = ['-posted_at']
    can_delete = True
    max_num = 0

    def caption_preview(self, obj):
        return obj.caption[:120] + '…' if len(obj.caption) > 120 else obj.caption
    caption_preview.short_description = 'Caption'

    def permalink_link(self, obj):
        return format_html('<a href="{}" target="_blank">Open ↗</a>', obj.permalink)
    permalink_link.short_description = 'Link'


@admin.register(InstagramAccount)
class InstagramAccountAdmin(admin.ModelAdmin):
    list_display   = ['handle', 'display_name', 'status_badge', 'follower_count', 'post_count', 'last_fetched']
    list_filter    = ['status']
    search_fields  = ['handle', 'display_name', 'bio', 'notes']
    readonly_fields = ['ig_user_id', 'display_name', 'bio', 'follower_count', 'last_fetched']
    list_editable  = ['status'] if False else []  # avoid accidental bulk edits; use actions
    inlines        = [InstagramPostInline]
    actions        = ['approve_selected', 'reject_selected', 'harvest_selected']
    fieldsets = [
        (None, {'fields': ['handle', 'status', 'notes']}),
        ('Fetched data', {'fields': ['ig_user_id', 'display_name', 'bio', 'follower_count', 'last_fetched'],
                          'classes': ['collapse']}),
    ]

    def post_count(self, obj):
        return obj.posts.count()
    post_count.short_description = 'Posts'

    def status_badge(self, obj):
        colours = {
            'pending':  '#e6a817',
            'active':   '#2e7d32',
            'rejected': '#888',
        }
        colour = colours.get(obj.status, '#888')
        return format_html(
            '<span style="color:{};font-weight:bold">{}</span>',
            colour, obj.get_status_display()
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'

    def approve_selected(self, request, queryset):
        n = queryset.update(status=InstagramAccount.STATUS_ACTIVE)
        self.message_user(request, f'{n} account(s) approved for harvesting.')
    approve_selected.short_description = '✅ Approve — start harvesting'

    def reject_selected(self, request, queryset):
        n = queryset.update(status=InstagramAccount.STATUS_REJECTED)
        self.message_user(request, f'{n} account(s) rejected.')
    reject_selected.short_description = '🚫 Reject — skip this account'

    def harvest_selected(self, request, queryset):
        from django.core.management import call_command
        harvested = 0
        for account in queryset.filter(status=InstagramAccount.STATUS_ACTIVE):
            call_command('harvest_instagram', '--handle', account.handle, '--force')
            harvested += 1
        self.message_user(request, f'Harvested {harvested} active account(s).')
    harvest_selected.short_description = '📥 Harvest posts now'


@admin.register(InstagramPost)
class InstagramPostAdmin(admin.ModelAdmin):
    list_display  = ['shortcode', 'account', 'caption_preview', 'is_video', 'posted_at']
    list_filter   = ['account', 'is_video']
    search_fields = ['caption', 'shortcode', 'account__handle']
    readonly_fields = ['ig_post_id', 'shortcode', 'account', 'is_video', 'posted_at', 'fetched_at', 'permalink_link']
    ordering      = ['-posted_at']

    def caption_preview(self, obj):
        return obj.caption[:100] + '…' if len(obj.caption) > 100 else obj.caption
    caption_preview.short_description = 'Caption'

    def permalink_link(self, obj):
        return format_html('<a href="{}" target="_blank">instagram.com/p/{}</a>', obj.permalink, obj.shortcode)
    permalink_link.short_description = 'Permalink'
