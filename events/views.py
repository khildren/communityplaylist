from django.shortcuts import render, get_object_or_404, redirect
from django.db import models
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.utils.timezone import localtime
from django.db.models import Prefetch
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import admin, messages
from django.template.response import TemplateResponse
from .models import Event, EventPhoto, Genre, Artist, SiteStats, CalendarFeed, Venue, Neighborhood, UserProfile, Follow, EditSuggestion
from .forms import EventSubmitForm, EventPhotoForm, RegisterForm, StyledAuthForm, VenueForm
from .geocode import geocode_location
from urllib.parse import quote
from datetime import timedelta
import requests
import math
import re

# Portland city center
PDX_LAT, PDX_LNG = 45.5051, -122.6750

def haversine_miles(lat1, lng1, lat2, lng2):
    """Great-circle distance in miles between two lat/lng points."""
    R = 3958.8  # Earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1487255057257857105/tv3mMFyLyx86r4sKma-1zFvM-4-qt43jhWqf7nJm3N_LzAvq3ZWIVpmNTL5LeKUKKBiz"

def notify_discord(message):
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": message})
    except:
        pass


def event_list(request):
    now = timezone.now()
    flyer_prefetch = Prefetch(
        'photos',
        queryset=EventPhoto.objects.filter(approved=True).order_by('created_at'),
        to_attr='approved_photos',
    )
    events = Event.objects.filter(status='approved').order_by('start_date').prefetch_related(flyer_prefetch)

    genre_id = request.GET.get('genre')
    neighborhood = request.GET.get('neighborhood')
    date_range = request.GET.get('date', 'future')
    free_only = request.GET.get('free')
    event_type = request.GET.get('event_type', '')  # online, local, or blank = both
    category = request.GET.get('category', '')
    search_query = request.GET.get('q', '').strip()
    radius = request.GET.get('radius', '')  # '15', '30', '60' or blank = all

    if search_query:
        from django.db.models import Q
        events = events.filter(
            Q(title__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(location__icontains=search_query)
        )

    if genre_id:
        events = events.filter(genres__id=genre_id)
    if category:
        events = events.filter(category=category)
    if neighborhood:
        events = events.filter(neighborhood__icontains=neighborhood)
    if free_only:
        events = events.filter(is_free=True)
    if event_type == 'online':
        events = events.filter(location__iregex=r'^(https?://|www\.)')
    elif event_type == 'local':
        events = events.exclude(location__iregex=r'^(https?://|www\.)')

    # Distance filter is now client-side (uses browser geolocation, not PDX center)
    radius_miles = None  # kept for template context only

    if date_range == 'today':
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        events = events.filter(start_date__gte=today_start, start_date__lte=today_end)
    elif date_range == 'week':
        events = events.filter(start_date__gte=now, start_date__lte=now + timezone.timedelta(days=7))
    elif date_range == 'month':
        events = events.filter(start_date__gte=now, start_date__lte=now + timezone.timedelta(days=30))
    elif date_range == 'past':
        events = events.filter(start_date__lt=now).order_by('-start_date')
    else:  # 'future' or default
        events = events.filter(start_date__gte=now)

    neighborhoods = Event.objects.filter(
        status='approved', start_date__gte=now
    ).exclude(neighborhood='').values_list('neighborhood', flat=True).distinct().order_by('neighborhood')

    genres = Genre.objects.filter(
        events__status='approved', events__start_date__gte=now
    ).distinct().order_by('name')

    # Force eval now so approved_photos is populated for map_events build
    events_list = list(events)

    map_events = [
        {
            'title': e.title,
            'latitude': e.latitude,
            'longitude': e.longitude,
            'slug': e.slug,
            'location': e.location,
            'start_date': localtime(e.start_date).strftime('%b %d @ %I:%M %p'),
            'start_ts': int(localtime(e.start_date).timestamp()),
            'flyer_url': (e.approved_photos[0].image.url if e.approved_photos else (e.photo.url if e.photo else '')),
            'category': e.category or '',
        }
        for e in events_list
        if e.latitude is not None
    ]

    SiteStats.record_visit(request)
    visit_count = f"{SiteStats.get_count():,}"

    from board.models import BannerMessage
    banners = list(BannerMessage.objects.filter(active=True).order_by('created_at'))

    # {name_lower: slug} for neighborhood page links in event cards
    neighborhood_pages = {
        n.name.lower(): n.slug
        for n in Neighborhood.objects.filter(active=True).only('name', 'slug')
    }

    # Events happening right now: started, not yet ended (assume 3h if no end_date)
    happening_now = Event.objects.filter(
        status='approved',
        start_date__lte=now,
    ).filter(
        models.Q(end_date__gte=now) |
        models.Q(end_date__isnull=True, start_date__gte=now - timedelta(hours=3))
    ).order_by('start_date')

    return render(request, 'events/event_list.html', {
        'events': events_list,
        'neighborhoods': neighborhoods,
        'genres': genres,
        'map_events': map_events,
        'selected_genre': genre_id,
        'selected_neighborhood': neighborhood,
        'selected_date': date_range,
        'selected_event_type': event_type,
        'selected_category': category,
        'free_only': free_only,
        'visit_count': visit_count,
        'search_query': search_query,
        'banners': banners,
        'happening_now': happening_now,
        'selected_radius': radius,
        'neighborhood_pages': neighborhood_pages,
    })


def event_archive(request):
    now = timezone.now()
    events = Event.objects.filter(status='approved', start_date__lt=now).order_by('-start_date')

    genre_id = request.GET.get('genre')
    neighborhood = request.GET.get('neighborhood')
    free_only = request.GET.get('free')

    if genre_id:
        events = events.filter(genres__id=genre_id)
    if neighborhood:
        events = events.filter(neighborhood__icontains=neighborhood)
    if free_only:
        events = events.filter(is_free=True)

    neighborhoods = Event.objects.filter(
        status='approved', start_date__lt=now
    ).exclude(neighborhood='').values_list('neighborhood', flat=True).distinct().order_by('neighborhood')

    genres = Genre.objects.filter(
        events__status='approved', events__start_date__lt=now
    ).distinct().order_by('name')

    map_events = [
        {
            'title': e.title,
            'latitude': e.latitude,
            'longitude': e.longitude,
            'slug': e.slug,
            'location': e.location,
            'start_date': localtime(e.start_date).strftime('%b %d @ %I:%M %p'),
        }
        for e in events.exclude(latitude=None)
    ]

    return render(request, 'events/archive.html', {
        'events': events,
        'neighborhoods': neighborhoods,
        'genres': genres,
        'map_events': map_events,
        'selected_genre': genre_id,
        'selected_neighborhood': neighborhood,
        'free_only': free_only,
    })


def event_detail(request, slug):
    event = get_object_or_404(Event, slug=slug, status='approved')

    # Session-gated view count — no user tracking, just a counter
    session_key = f'viewed_event_{event.pk}'
    if not request.session.get(session_key):
        Event.objects.filter(pk=event.pk).update(view_count=models.F('view_count') + 1)
        request.session[session_key] = True
        event.view_count += 1  # reflect in template without re-fetching

    photos = event.photos.filter(approved=True)
    photo_form = EventPhotoForm()
    upload_success = False

    if request.method == 'POST':
        photo_form = EventPhotoForm(request.POST, request.FILES)
        if photo_form.is_valid():
            photo = photo_form.save(commit=False)
            photo.event = event
            photo.photo_type = 'recap'
            photo.approved = True
            photo.save()
            upload_success = True
            photos = event.photos.filter(approved=True)

    # Build Google Calendar add-event link
    start_str = localtime(event.start_date).strftime('%Y%m%dT%H%M%S')
    end_dt = event.end_date if event.end_date else event.start_date + timedelta(hours=2)
    end_str = localtime(end_dt).strftime('%Y%m%dT%H%M%S')
    cal_url = (
        f"https://www.google.com/calendar/render?action=TEMPLATE"
        f"&text={quote(event.title)}"
        f"&dates={start_str}/{end_str}"
        f"&details={quote(event.description[:500])}"
        f"&location={quote(event.location)}"
    )

    # Build location link — use URL directly if one was entered, otherwise OpenStreetMap
    loc = event.location
    if loc.startswith('www.'):
        maps_url = f'https://{loc}'
    elif loc.startswith(('http://', 'https://')):
        maps_url = loc
    elif event.latitude and event.longitude:
        maps_url = f"https://www.openstreetmap.org/directions?to={event.latitude},{event.longitude}"
    else:
        maps_url = f"https://www.openstreetmap.org/search?query={quote(loc)}"

    # Split photos: first approved photo is the flyer, rest are gallery
    photos_list = list(photos)
    flyer_photo = photos_list[0] if photos_list else None
    gallery_photos = photos_list[1:] if len(photos_list) > 1 else []

    # Recurring series — linked via FK (submitted form) OR same title+location (iCal imports)
    recurring_instances = []
    if event.recurring_event_id:
        recurring_instances = list(
            Event.objects.filter(
                recurring_event_id=event.recurring_event_id,
                status='approved',
            ).order_by('start_date')
        )
    elif event.location:
        # Auto-imported series: same title root (strip trailing date/number variants) + same location
        # Match on exact title + exact location — reliable for iCal series like Gnosis
        same_series = Event.objects.filter(
            title=event.title,
            location=event.location,
            status='approved',
        ).order_by('start_date')
        if same_series.count() > 1:
            recurring_instances = list(same_series)

    venue = Venue.for_location(event.location)

    return render(request, 'events/event_detail.html', {
        'event': event,
        'photos': photos,
        'flyer_photo': flyer_photo,
        'gallery_photos': gallery_photos,
        'photo_form': photo_form,
        'upload_success': upload_success,
        'cal_url': cal_url,
        'maps_url': maps_url,
        'recurring_instances': recurring_instances,
        'now': timezone.now(),
        'venue': venue,
        'event_edit_fields': EditSuggestion.FIELDS['event'],
    })


def event_submit(request):
    if request.method == 'POST':
        form = EventSubmitForm(request.POST, request.FILES)
        if form.is_valid():
            event = form.save(commit=False)
            lat, lng = geocode_location(event.location)
            event.latitude = lat
            event.longitude = lng
            extra = [u.strip() for u in request.POST.getlist('extra_links') if u.strip()]
            event.extra_links = extra[:10]
            if request.user.is_authenticated:
                event.submitted_user = request.user
            event.save()
            form.save_m2m()
            genre_ids = request.POST.getlist('genre_ids')
            if genre_ids:
                event.genres.set(Genre.objects.filter(id__in=genre_ids))
            artist_ids = request.POST.getlist('artist_ids')
            if artist_ids:
                event.artists.set(Artist.objects.filter(id__in=artist_ids))

            # Handle recurring event submission
            if request.POST.get('is_recurring'):
                from events.models import RecurringEvent
                freq         = request.POST.get('recur_frequency', 'weekly')
                interval     = max(1, int(request.POST.get('recur_interval', 1) or 1))
                day_of_week  = request.POST.get('recur_day_of_week')
                week_of_month = request.POST.get('recur_week_of_month')
                rec = RecurringEvent.objects.create(
                    title           = event.title,
                    description     = event.description,
                    location        = event.location,
                    category        = event.category,
                    is_free         = event.is_free,
                    price_info      = event.price_info,
                    website         = event.website,
                    frequency       = freq,
                    interval        = interval,
                    day_of_week     = int(day_of_week) if day_of_week is not None else None,
                    week_of_month   = int(week_of_month) if week_of_month else None,
                    start_time      = event.start_date.time(),
                    duration_minutes = int((event.end_date - event.start_date).total_seconds() // 60) if event.end_date else 120,
                    submitted_by    = event.submitted_by,
                    submitted_email = event.submitted_email,
                    submitted_user  = event.submitted_user,
                    auto_approve    = False,
                )
                if genre_ids:
                    rec.genres.set(Genre.objects.filter(id__in=genre_ids))
                if artist_ids:
                    rec.residents.set(Artist.objects.filter(id__in=artist_ids))
                event.recurring_event = rec
                event.save(update_fields=['recurring_event'])

            notify_discord(
                f"🎵 **New event submitted for review!**\n"
                f"**{event.title}**\n"
                f"📅 {localtime(event.start_date).strftime('%b %d %Y @ %I:%M %p')}\n"
                f"📍 {event.location}\n"
                f"👤 Submitted by: {event.submitted_by or 'Anonymous'}\n"
                f"🔗 https://communityplaylist.com/admin/events/event/{event.id}/change/"
            )
            return render(request, 'events/submit_thanks.html')
    else:
        form = EventSubmitForm()
    return render(request, 'events/submit.html', {'form': form})


def genre_autocomplete(request):
    q = request.GET.get('q', '')
    if len(q) < 2:
        return JsonResponse([], safe=False)
    genres = Genre.objects.filter(name__icontains=q)[:10]
    return JsonResponse([{'id': g.id, 'name': g.name} for g in genres], safe=False)


def artist_autocomplete(request):
    """Local DB search + MusicBrainz fallback. Returns found=False flag when nothing found."""
    q = request.GET.get('q', '')
    if len(q) < 2:
        return JsonResponse([], safe=False)

    local = list(Artist.objects.filter(name__icontains=q)[:10])
    if local:
        return JsonResponse([{'id': a.id, 'name': a.name, 'mb_id': a.mb_id} for a in local], safe=False)

    # Fallback: search MusicBrainz
    try:
        resp = requests.get(
            'https://musicbrainz.org/ws/2/artist',
            params={'query': q, 'fmt': 'json', 'limit': 8},
            headers={'User-Agent': 'CommunityPlaylist/1.0 (hello@communityplaylist.com)', 'Accept': 'application/json'},
            timeout=5,
        )
        data = resp.json()
        results = []
        for a in data.get('artists', []):
            name = a.get('name', '')
            mb_id = a.get('id', '')
            artist, _ = Artist.objects.get_or_create(name=name, defaults={'mb_id': mb_id})
            # Auto-link local-only artists if MusicBrainz now has them
            if _ is False and not artist.mb_id and mb_id:
                Artist.objects.filter(pk=artist.pk).update(mb_id=mb_id)
                artist.mb_id = mb_id
            results.append({'id': artist.id, 'name': artist.name, 'mb_id': artist.mb_id})
        return JsonResponse(results, safe=False)
    except Exception:
        return JsonResponse([], safe=False)


def artist_profile(request, pk):
    artist = get_object_or_404(Artist, pk=pk)
    now = timezone.now()
    upcoming  = artist.events.filter(status='approved', start_date__gte=now).order_by('start_date')
    past      = artist.events.filter(status='approved', start_date__lt=now).order_by('-start_date')[:20]
    recurring = artist.recurring_events.filter(active=True)
    is_following = (
        request.user.is_authenticated and
        Follow.objects.filter(user=request.user, target_type=Follow.TYPE_ARTIST, target_id=artist.pk).exists()
    )
    return render(request, 'events/artist_profile.html', {
        'artist': artist, 'upcoming': upcoming, 'past': past, 'recurring': recurring,
        'is_following': is_following,
        'artist_edit_fields': EditSuggestion.FIELDS['artist'],
    })


def artist_add(request):
    """Create a local-only artist record (no MusicBrainz ID yet)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    import json as _json
    try:
        body = _json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    name = body.get('name', '').strip()[:200]
    if not name:
        return JsonResponse({'error': 'Name required'}, status=400)
    artist, created = Artist.objects.get_or_create(name=name)
    return JsonResponse({'id': artist.id, 'name': artist.name, 'mb_id': artist.mb_id, 'created': created})


# ── Auth views ──

def _send_verification_email(user, profile):
    """Send (or resend) the email-verification link."""
    from django.core.mail import send_mail
    from django.conf import settings
    token = UserProfile.generate_token()
    profile.verify_token = token
    profile.save(update_fields=['verify_token'])
    url = f"{settings.SITE_URL}/verify-email/{token}/"
    send_mail(
        subject='Verify your Community Playlist email',
        message=(
            f"Hi {user.email},\n\n"
            f"Click the link below to verify your email address:\n\n"
            f"  {url}\n\n"
            f"If you didn't create an account, ignore this email.\n\n"
            f"— Community Playlist\n"
            f"   communityplaylist.com"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )


def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = RegisterForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        from django.contrib.auth.models import User
        email = form.cleaned_data['email'].lower()
        user = User.objects.create_user(
            username=email,
            email=email,
            password=form.cleaned_data['password'],
        )
        # Create profile with auto-generated handle
        profile = UserProfile.objects.create(
            user=user,
            handle=UserProfile.handle_from_email(email),
        )
        _send_verification_email(user, profile)
        # Auto-claim any events submitted with this email
        claimed = Event.objects.filter(submitted_email__iexact=email, submitted_user=None)
        claimed.update(submitted_user=user)
        login(request, user)
        messages.success(request, f'Welcome! Check your email to verify your account.')
        return redirect('dashboard')
    return render(request, 'accounts/register.html', {'form': form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = StyledAuthForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        login(request, form.get_user())
        return redirect(request.GET.get('next', 'dashboard'))
    return render(request, 'accounts/login.html', {'form': form})


def logout_view(request):
    logout(request)
    return redirect('event_list')


@login_required(login_url='/login/')
def dashboard(request):
    # Handle calendar feed add/remove
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_feed':
            urls   = [u.strip() for u in request.POST.getlist('feed_url') if u.strip()]
            labels = request.POST.getlist('feed_label')
            for i, url in enumerate(urls[:10]):
                label = labels[i] if i < len(labels) else ''
                if not CalendarFeed.objects.filter(user=request.user, url=url).exists():
                    CalendarFeed.objects.create(user=request.user, url=url, label=label.strip()[:100])
            messages.success(request, 'Feed(s) saved.')
        elif action == 'remove_feed':
            feed_id = request.POST.get('feed_id')
            CalendarFeed.objects.filter(pk=feed_id, user=request.user).delete()
        return redirect('dashboard')

    events  = Event.objects.filter(submitted_user=request.user).order_by('-created_at')
    feeds   = CalendarFeed.objects.filter(user=request.user)
    profile, _ = UserProfile.objects.get_or_create(
        user=request.user,
        defaults={'handle': UserProfile.handle_from_email(request.user.email)},
    )
    follows = Follow.objects.filter(user=request.user).select_related()
    follow_data = [{'follow': f, 'target': f.get_target()} for f in follows]
    follow_data = [x for x in follow_data if x['target'] is not None]
    return render(request, 'accounts/dashboard.html', {
        'events': events,
        'feeds': feeds,
        'profile': profile,
        'follow_data': follow_data,
    })


@login_required(login_url='/login/')
def event_edit(request, slug):
    event = get_object_or_404(Event, slug=slug, submitted_user=request.user)
    form = EventSubmitForm(request.POST or None, request.FILES or None, instance=event)
    if request.method == 'POST' and form.is_valid():
        ev = form.save(commit=False)
        extra = [u.strip() for u in request.POST.getlist('extra_links') if u.strip()]
        ev.extra_links = extra[:10]
        # Re-geocode if location changed
        if 'location' in form.changed_data:
            lat, lng = geocode_location(ev.location)
            ev.latitude = lat
            ev.longitude = lng
        ev.save()
        form.save_m2m()
        genre_ids = request.POST.getlist('genre_ids')
        if genre_ids:
            ev.genres.set(Genre.objects.filter(id__in=genre_ids))
        messages.success(request, 'Event updated.')
        return redirect('dashboard')
    return render(request, 'accounts/event_edit.html', {'form': form, 'event': event})


@login_required(login_url='/login/')
def claim_event(request, slug):
    event = get_object_or_404(Event, slug=slug)
    if event.submitted_user:
        messages.error(request, 'This event has already been claimed.')
        return redirect('event_detail', slug=slug)
    user_email = request.user.email.lower()
    if event.submitted_email.lower() != user_email:
        messages.error(request, 'The email on your account does not match the submission email for this event.')
        return redirect('event_detail', slug=slug)
    event.submitted_user = request.user
    event.save()
    messages.success(request, 'Event claimed! You can now manage it from your dashboard.')
    return redirect('dashboard')


# ── Calendar feed ──

def calendar_feed(request):
    from icalendar import Calendar, Event as IEvent, vText, vDatetime
    from datetime import datetime

    now = timezone.now()
    events = Event.objects.filter(status='approved', start_date__gte=now).order_by('start_date')

    # Optional filters
    category = request.GET.get('category')
    genre_id = request.GET.get('genre')
    free_only = request.GET.get('free')
    if category:
        events = events.filter(category=category)
    if genre_id:
        events = events.filter(genres__id=genre_id)
    if free_only:
        events = events.filter(is_free=True)

    cal = Calendar()
    cal.add('prodid', '-//Community Playlist//communityplaylist.com//')
    cal.add('version', '2.0')
    cal.add('x-wr-calname', 'Community Playlist — PDX Events')
    cal.add('x-wr-timezone', 'America/Los_Angeles')
    cal.add('x-wr-caldesc', 'Portland community events submitted by the people, for the people.')
    cal.add('refresh-interval', 'PT1H')

    for event in events:
        ie = IEvent()
        ie.add('uid', f'{event.slug}@communityplaylist.com')
        ie.add('summary', event.title)
        ie.add('dtstart', event.start_date)
        ie.add('dtend', event.end_date or (event.start_date + timezone.timedelta(hours=2)))
        ie.add('dtstamp', now)
        ie.add('description', event.description[:500] if event.description else '')
        if event.location and not event.location.startswith(('http', 'www')):
            ie.add('location', event.location)
        ie.add('url', f'https://communityplaylist.com/events/{event.slug}/')
        if event.is_free:
            ie.add('categories', ['Free'])
        if event.category:
            ie.add('categories', [event.get_category_display()])
        cal.add_component(ie)

    resp = HttpResponse(cal.to_ical(), content_type='text/calendar; charset=utf-8')
    resp['Content-Disposition'] = 'attachment; filename="communityplaylist.ics"'
    return resp


def calendar_subscribe(request):
    genres = Genre.objects.filter(
        events__status='approved', events__start_date__gte=timezone.now()
    ).distinct().order_by('name')
    return render(request, 'events/calendar_subscribe.html', {'genres': genres})


def features_page(request):
    return render(request, 'events/features.html')


# ── Venue profiles ──

def venue_list(request):
    venues = Venue.objects.filter(active=True, verified=True).order_by('name')
    return render(request, 'events/venue_list.html', {'venues': venues})


def venue_detail(request, slug):
    venue = get_object_or_404(Venue, slug=slug, active=True)
    now = timezone.now()

    session_key = f'viewed_venue_{venue.pk}'
    if not request.session.get(session_key):
        Venue.objects.filter(pk=venue.pk).update(view_count=models.F('view_count') + 1)
        request.session[session_key] = True
        venue.view_count += 1

    all_events = venue.get_events()
    upcoming = all_events.filter(start_date__gte=now)[:30]
    past     = all_events.filter(start_date__lt=now).order_by('-start_date')[:20]
    is_following = (
        request.user.is_authenticated and
        Follow.objects.filter(user=request.user, target_type=Follow.TYPE_VENUE, target_id=venue.pk).exists()
    )
    return render(request, 'events/venue_detail.html', {
        'venue': venue,
        'upcoming': upcoming,
        'past': past,
        'now': now,
        'is_following': is_following,
        'venue_edit_fields': EditSuggestion.FIELDS['venue'],
    })


def venue_feed(request, slug):
    from icalendar import Calendar, Event as IEvent
    venue = get_object_or_404(Venue, slug=slug, active=True)
    now = timezone.now()
    events = venue.get_events().filter(start_date__gte=now)

    cal = Calendar()
    cal.add('prodid', f'-//Community Playlist//{venue.name}//communityplaylist.com//')
    cal.add('version', '2.0')
    cal.add('x-wr-calname', f'{venue.name} — Community Playlist')
    cal.add('x-wr-timezone', 'America/Los_Angeles')
    cal.add('x-wr-caldesc', f'Upcoming events at {venue.name} via communityplaylist.com')
    cal.add('refresh-interval', 'PT1H')

    for event in events:
        ie = IEvent()
        ie.add('uid', f'{event.slug}@communityplaylist.com')
        ie.add('summary', event.title)
        ie.add('dtstart', event.start_date)
        ie.add('dtend', event.end_date or (event.start_date + timezone.timedelta(hours=2)))
        ie.add('dtstamp', now)
        ie.add('description', event.description[:500] if event.description else '')
        if event.location and not event.location.startswith(('http', 'www')):
            ie.add('location', event.location)
        ie.add('url', f'https://communityplaylist.com/events/{event.slug}/')
        if event.is_free:
            ie.add('categories', ['Free'])
        if event.category:
            ie.add('categories', [event.get_category_display()])
        cal.add_component(ie)

    resp = HttpResponse(cal.to_ical(), content_type='text/calendar; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="{venue.slug}.ics"'
    return resp


@login_required(login_url='/login/')
def venue_register(request):
    # Check if user already claimed a venue
    existing = Venue.objects.filter(claimed_by=request.user).first()
    if existing:
        return redirect('venue_detail', slug=existing.slug)

    form = VenueForm(request.POST or None, request.FILES or None)
    claim_conflict = None  # already claimed by someone else

    if request.method == 'POST' and form.is_valid():
        submitted_name    = form.cleaned_data['name'].strip().lower()
        submitted_address = form.cleaned_data.get('address', '').strip().lower()

        # Look for an existing unclaimed venue that matches on name or address
        match = None
        for candidate in Venue.objects.filter(active=True):
            name_match = candidate.name.strip().lower() == submitted_name
            addr_match = (submitted_address and candidate.address
                          and submitted_address[:30] in candidate.address.lower())
            if name_match or addr_match:
                if candidate.claimed_by is None:
                    match = candidate
                elif candidate.claimed_by != request.user:
                    claim_conflict = candidate
                break

        if claim_conflict:
            # Already owned by someone else — show error, don't create duplicate
            messages.error(request,
                f'"{claim_conflict.name}" is already claimed. '
                'If you own this venue, contact us to resolve the conflict.')
            return render(request, 'events/venue_register.html', {'form': form})

        if match:
            # Existing unclaimed profile — assign ownership, preserve verified status
            match.claimed_by = request.user
            # Fill in any blank fields from the submitted form without overwriting
            for field in ('description', 'website', 'instagram', 'twitter',
                          'bluesky', 'threads', 'neighborhood'):
                submitted_val = form.cleaned_data.get(field, '')
                if submitted_val and not getattr(match, field):
                    setattr(match, field, submitted_val)
            if 'logo' in request.FILES and not match.logo:
                match.logo = request.FILES['logo']
            match.save()
            notify_discord(
                f"🏛 **Venue profile claimed!**\n"
                f"**{match.name}** (existing {'✓ verified' if match.verified else 'unverified'} profile)\n"
                f"👤 Claimed by: {request.user.email}\n"
                f"🔗 https://communityplaylist.com/admin/events/venue/{match.id}/change/"
            )
            messages.success(request,
                f'You\'ve claimed "{match.name}"!'
                + (' It was already verified — you\'re live.' if match.verified
                   else ' We\'ll verify it shortly.'))
            return redirect('venue_detail', slug=match.slug)

        # No match — create new venue profile
        venue = form.save(commit=False)
        venue.claimed_by = request.user
        lat, lng = geocode_location(venue.address)
        venue.latitude = lat
        venue.longitude = lng
        venue.save()
        notify_discord(
            f"🏛 **New venue profile submitted!**\n"
            f"**{venue.name}**\n"
            f"📍 {venue.address}\n"
            f"👤 Claimed by: {request.user.email}\n"
            f"🔗 https://communityplaylist.com/admin/events/venue/{venue.id}/change/"
        )
        messages.success(request, 'Venue submitted! We\'ll verify it shortly.')
        return redirect('venue_detail', slug=venue.slug)

    return render(request, 'events/venue_register.html', {'form': form})


@login_required(login_url='/login/')
def venue_edit(request, slug):
    venue = get_object_or_404(Venue, slug=slug, claimed_by=request.user)
    form = VenueForm(request.POST or None, request.FILES or None, instance=venue)
    if request.method == 'POST' and form.is_valid():
        v = form.save(commit=False)
        if 'address' in form.changed_data:
            lat, lng = geocode_location(v.address)
            v.latitude = lat
            v.longitude = lng
        v.save()
        messages.success(request, 'Venue updated.')
        return redirect('venue_detail', slug=venue.slug)
    return render(request, 'events/venue_register.html', {'form': form, 'venue': venue, 'editing': True})


# ── Neighborhood pages ────────────────────────────────────────────────────────

def neighborhood_list(request):
    """All active neighborhoods with upcoming event counts."""
    now = timezone.now()
    hoods = Neighborhood.objects.filter(active=True)
    # Annotate with upcoming event count
    from django.db.models import Count
    hood_data = []
    for h in hoods:
        count = Event.objects.filter(
            status='approved',
            start_date__gte=now,
        ).filter(h.event_q()).count()
        hood_data.append({'hood': h, 'count': count})
    # Sort by event count descending, then name
    hood_data.sort(key=lambda x: (-x['count'], x['hood'].name))
    return render(request, 'events/neighborhood_list.html', {
        'hood_data': hood_data,
    })


def neighborhood_detail(request, slug):
    """Neighborhood page: history blurb, upcoming events, community board."""
    from board.models import Topic, Reply
    hood = get_object_or_404(Neighborhood, slug=slug, active=True)
    now  = timezone.now()

    upcoming = Event.objects.filter(
        status='approved',
        start_date__gte=now,
    ).filter(hood.event_q()).order_by('start_date')[:30]

    # Board topics tagged to this neighborhood
    topics = Topic.objects.filter(neighborhood=hood).order_by('-pinned', '-created_at')[:20]

    # Handle new topic post
    post_error = None
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'new_topic':
            title  = request.POST.get('title', '').strip()[:200]
            body   = request.POST.get('body', '').strip()[:2000]
            author = request.POST.get('author_name', '').strip()[:80] or 'Anonymous'
            category = request.POST.get('category', 'general')
            valid_cats = {'general', 'aid', 'announce', 'question'}
            if category not in valid_cats:
                category = 'general'
            if title and body:
                from board.spam import check_post
                ok, err = check_post(title=title, body=body, user=request.user)
                if not ok:
                    post_error = err
                else:
                    Topic.objects.create(
                        title=title,
                        body=body,
                        author_name=author,
                        category=category,
                        neighborhood=hood,
                    )
                    return redirect('neighborhood_detail', slug=slug)
            else:
                post_error = 'Title and message are required.'
        elif action == 'reply':
            topic_pk = request.POST.get('topic_pk')
            body     = request.POST.get('body', '').strip()[:2000]
            author   = request.POST.get('author_name', '').strip()[:80] or 'Anonymous'
            if topic_pk and body:
                from board.spam import check_post
                ok, err = check_post(body=body, user=request.user)
                if ok:
                    topic = get_object_or_404(Topic, pk=topic_pk, neighborhood=hood)
                    Reply.objects.create(topic=topic, body=body, author_name=author)
                    return redirect('neighborhood_detail', slug=slug)
                else:
                    post_error = err

    is_following = (
        request.user.is_authenticated and
        Follow.objects.filter(user=request.user, target_type=Follow.TYPE_NEIGHBORHOOD, target_id=hood.pk).exists()
    )
    return render(request, 'events/neighborhood_detail.html', {
        'hood': hood,
        'upcoming': upcoming,
        'topics': topics,
        'post_error': post_error,
        'now': now,
        'is_following': is_following,
        'hood_edit_fields': EditSuggestion.FIELDS['neighborhood'],
    })


# ── User profiles ─────────────────────────────────────────────────────────────

def verify_email(request, token):
    profile = UserProfile.objects.filter(verify_token=token).first()
    if not profile:
        messages.error(request, 'Invalid or expired verification link.')
        return redirect('dashboard' if request.user.is_authenticated else 'login')
    profile.email_verified = True
    profile.verify_token = ''
    profile.save(update_fields=['email_verified', 'verify_token'])
    messages.success(request, 'Email verified!')
    return redirect('dashboard')


@login_required(login_url='/login/')
def resend_verification(request):
    profile, _ = UserProfile.objects.get_or_create(
        user=request.user,
        defaults={'handle': UserProfile.handle_from_email(request.user.email)},
    )
    if profile.email_verified:
        messages.info(request, 'Your email is already verified.')
    else:
        _send_verification_email(request.user, profile)
        messages.success(request, 'Verification email sent — check your inbox.')
    return redirect('dashboard')


@login_required(login_url='/login/')
def profile_settings(request):
    profile, _ = UserProfile.objects.get_or_create(
        user=request.user,
        defaults={'handle': UserProfile.handle_from_email(request.user.email)},
    )
    error = None
    if request.method == 'POST':
        handle = request.POST.get('handle', '').strip().lower()
        # Validate handle
        if not re.match(r'^[a-z0-9_]{2,50}$', handle):
            error = 'Handle must be 2–50 characters: lowercase letters, numbers, underscores only.'
        elif UserProfile.objects.filter(handle=handle).exclude(pk=profile.pk).exists():
            error = 'That handle is already taken.'
        else:
            profile.handle   = handle
            profile.pronouns = request.POST.get('pronouns', '').strip()[:40]
            profile.bio      = request.POST.get('bio', '').strip()[:500]
            profile.is_public = bool(request.POST.get('is_public'))
            # Links: up to 5 {label, url} pairs
            labels = request.POST.getlist('link_label')[:5]
            urls   = request.POST.getlist('link_url')[:5]
            raw_links = [
                {'label': l.strip()[:60], 'url': u.strip()[:200]}
                for l, u in zip(labels, urls) if u.strip()
            ]
            # Cache embed HTML for Bandcamp/SoundCloud at save time (not at view time)
            # Preserve existing embed_html if URL hasn't changed
            old_links = {lk['url']: lk for lk in (profile.links or [])}
            enriched = []
            for lk in raw_links:
                url = lk['url']
                cached_html = old_links.get(url, {}).get('embed_html', '')
                is_yt_chan = 'youtube.com' in url and ('/@' in url or '/channel/UC' in url or '/c/' in url)
                if cached_html and not (is_yt_chan and 'yt-uploads-player' not in cached_html):
                    lk['embed_html'] = cached_html
                elif ('bandcamp.com' in url or 'soundcloud.com' in url or
                      ('youtube.com' in url and ('/@' in url or '/channel/UC' in url or '/c/' in url))):
                    lk['embed_html'] = _fetch_embed_html(url) or ''
                enriched.append(lk)
            profile.links = enriched
            if 'avatar' in request.FILES:
                profile.avatar = request.FILES['avatar']
            profile.save()
            messages.success(request, 'Profile saved.')
            return redirect('profile_settings')
    return render(request, 'accounts/profile_settings.html', {
        'profile': profile,
        'error': error,
    })


def _fetch_embed_html(url, max_width=600):
    """
    Fetch embeddable HTML for Bandcamp, SoundCloud, or YouTube channel URLs.
    Returns HTML string or None.
    """
    import requests as _req
    import html as _html
    _HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; CommunityPlaylist/1.0; +https://communityplaylist.com)'}

    # ── YouTube channel (handle /@x, /channel/UCx, /c/x) ────────────
    if 'youtube.com' in url and ('/@' in url or '/channel/UC' in url or '/c/' in url):
        try:
            r = _req.get(url, timeout=8, headers=_HEADERS)
            if r.status_code != 200:
                return None

            # Avatar + channel name from OG tags
            avatar_m = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', r.text)
            name_m   = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', r.text)
            chan_name  = _html.unescape(name_m.group(1)) if name_m else ''
            avatar_url = avatar_m.group(1) if avatar_m else ''

            # Subscriber count (YouTube embeds it in page JSON)
            sub_m = re.search(r'"([\d.,]+\s*[KMB]?\s*subscribers?)"', r.text, re.I)
            if not sub_m:
                sub_m = re.search(r'"subscriberCountText"\s*:\s*\{"simpleText"\s*:\s*"([^"]+)"', r.text)
            sub_count = sub_m.group(1) if sub_m else ''

            # Channel ID → uploads playlist (UU prefix replaces UC)
            chan_id_m = re.search(r'feeds/videos\.xml\?channel_id=(UC[\w-]+)', r.text)
            uploads_playlist = ''
            if chan_id_m:
                uploads_playlist = 'UU' + chan_id_m.group(1)[2:]  # UCxxx → UUxxx

            sub_url = url.rstrip('/') + '?sub_confirmation=1'
            avatar_html = (
                f'<img src="{avatar_url}" style="width:44px;height:44px;border-radius:50%;'
                f'object-fit:cover;flex-shrink:0" alt="">'
                if avatar_url else
                '<div style="width:44px;height:44px;border-radius:50%;background:#333;flex-shrink:0"></div>'
            )
            sub_count_html = f'<div style="color:#888;font-size:.74em;margin-top:2px">{sub_count}</div>' if sub_count else ''
            player_div = (
                f'<div class="yt-uploads-player" data-uploads="{uploads_playlist}" '
                f'style="margin-top:12px;width:100%;aspect-ratio:16/9;background:#000;border-radius:4px"></div>'
                if uploads_playlist else ''
            )
            return (
                f'<div style="background:#0f0f0f;padding:14px 16px">'
                f'<div style="display:flex;align-items:center;gap:12px">'
                f'{avatar_html}'
                f'<div style="flex:1;min-width:0">'
                f'<div style="color:#fff;font-weight:600;font-size:.9em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{chan_name}</div>'
                f'{sub_count_html}'
                f'</div>'
                f'<a href="{sub_url}" target="_blank" rel="noopener" style="flex-shrink:0;display:inline-flex;'
                f'align-items:center;gap:5px;background:#f00;color:#fff;font-size:.78em;font-weight:700;'
                f'padding:6px 12px;border-radius:4px;text-decoration:none">&#9654; Subscribe</a>'
                f'</div>'
                f'{player_div}'
                f'</div>'
            )
        except Exception:
            return None

    try:
        if 'bandcamp.com' in url:
            is_album = '/album/' in url
            kind = 'album' if is_album else 'track'
            r = _req.get(url, timeout=8, headers=_HEADERS)
            if r.status_code != 200:
                return None
            # Look for the specific track/album ID in structured data contexts
            # Bandcamp puts it in patterns like: "id":2395489664 near "item_type":"t"
            # or as current_item = {"id":NNNN} / tralbum_data
            item_id = None
            # Try: current = {"id": NNN} or {"id":NNN,"type":"track"/"album"}
            m = re.search(r'"id"\s*:\s*(\d{7,12})\s*,\s*"(?:type|item_type)"\s*:\s*"(?:track|album|t|a)"', r.text)
            if m:
                item_id = m.group(1)
            if not item_id:
                # Try: "item_type":"t","id":NNN  (reversed key order)
                m = re.search(r'"(?:type|item_type)"\s*:\s*"(?:track|album|t|a)"[^}]{0,60}"id"\s*:\s*(\d{7,12})', r.text)
                if m:
                    item_id = m.group(1)
            if not item_id:
                # Try: EmbeddedPlayer URL already present in page source
                m = re.search(r'EmbeddedPlayer/' + kind + r'=(\d{7,12})', r.text)
                if m:
                    item_id = m.group(1)
            if not item_id:
                # Fallback: find the ID that actually loads the right player
                import collections
                candidates = collections.Counter(re.findall(r'\b(\d{9,11})\b', r.text)).most_common(5)
                for cand_id, _ in candidates:
                    test = _req.get(
                        f'https://bandcamp.com/EmbeddedPlayer/{kind}={cand_id}/size=small/bgcol=111111/linkcol=ff6b35/transparent=true/',
                        timeout=4, headers=_HEADERS
                    )
                    # The correct player page will contain the artist/track name
                    page_title = re.search(r'<title[^>]*>([^<]+)', r.text)
                    artist_hint = page_title.group(1).split('|')[0].strip().lower()[:15] if page_title else ''
                    if test.status_code == 200 and (not artist_hint or artist_hint in test.text.lower()):
                        item_id = cand_id
                        break
            if not item_id:
                return None
            if is_album:
                height = '400'
                embed_src = (f'https://bandcamp.com/EmbeddedPlayer/{kind}={item_id}'
                             f'/size=large/bgcol=111111/linkcol=ff6b35/tracklist=true/artwork=small/transparent=true/')
            else:
                height = '120'
                embed_src = (f'https://bandcamp.com/EmbeddedPlayer/{kind}={item_id}'
                             f'/size=large/bgcol=111111/linkcol=ff6b35/tracklist=false/artwork=small/transparent=true/')
            return (f'<iframe style="border:0;width:100%;height:{height}px" '
                    f'src="{embed_src}" seamless loading="lazy">'
                    f'<a href="{url}">Listen on Bandcamp</a></iframe>')

        if 'soundcloud.com' in url:
            r = _req.get(
                f'https://soundcloud.com/oembed?url={url}&format=json&maxwidth={max_width}',
                timeout=5, headers=_HEADERS,
            )
            if r.status_code == 200:
                html = r.json().get('html', '')
                if html:
                    return re.sub(r'width="\d+"', 'width="100%"', html)

    except Exception:
        pass
    return None


def public_profile(request, handle):
    profile = get_object_or_404(UserProfile, handle=handle, is_public=True)
    events  = Event.objects.filter(
        submitted_user=profile.user, status='approved',
        start_date__gte=timezone.now(),
    ).order_by('start_date')[:20]
    follows = Follow.objects.filter(user=profile.user).select_related()
    follow_data = [{'follow': f, 'target': f.get_target()} for f in follows]
    follow_data = [x for x in follow_data if x['target'] is not None]

    # Build embeds from cached embed_html stored in links at save time (no blocking HTTP)
    oembed_embeds = []
    for link in (profile.links or []):
        url = link.get('url', '')
        label = link.get('label', '')
        html = link.get('embed_html', '')
        if url and html:
            domain = 'bandcamp' if 'bandcamp.com' in url else 'soundcloud'
            oembed_embeds.append({'label': label, 'html': html, 'domain': domain})

    return render(request, 'accounts/public_profile.html', {
        'profile': profile,
        'events': events,
        'follow_data': follow_data,
        'oembed_embeds': oembed_embeds,
    })


# ── Follow toggle ─────────────────────────────────────────────────────────────

@login_required(login_url='/login/')
def toggle_follow(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    import json as _json
    try:
        body = _json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)
    target_type = body.get('type')
    target_id   = body.get('id')
    valid_types = {Follow.TYPE_ARTIST, Follow.TYPE_VENUE, Follow.TYPE_NEIGHBORHOOD}
    if target_type not in valid_types or not target_id:
        return JsonResponse({'error': 'invalid params'}, status=400)
    try:
        target_id = int(target_id)
    except (TypeError, ValueError):
        return JsonResponse({'error': 'invalid id'}, status=400)
    obj, created = Follow.objects.get_or_create(
        user=request.user, target_type=target_type, target_id=target_id,
    )
    if not created:
        obj.delete()
        return JsonResponse({'following': False})
    return JsonResponse({'following': True})


# ── RSS feed ──────────────────────────────────────────────────────────────────

def profile_feed(request, handle):
    """Atom/RSS feed of upcoming events from the user's followed entities."""
    from django.utils.feedgenerator import Rss201rev2Feed
    from django.db.models import Q
    profile = get_object_or_404(UserProfile, handle=handle, is_public=True)
    follows = Follow.objects.filter(user=profile.user)
    now     = timezone.now()

    artist_ids  = list(follows.filter(target_type=Follow.TYPE_ARTIST).values_list('target_id', flat=True))
    venue_ids   = list(follows.filter(target_type=Follow.TYPE_VENUE).values_list('target_id', flat=True))
    hood_ids    = list(follows.filter(target_type=Follow.TYPE_NEIGHBORHOOD).values_list('target_id', flat=True))

    qs = Event.objects.filter(status='approved', start_date__gte=now)
    q  = Q()
    if artist_ids:
        q |= Q(artists__id__in=artist_ids)
    if venue_ids:
        hoods = Neighborhood.objects.filter(pk__in=hood_ids)
        # Venue match via venue_name field (simple icontains per venue)
        for v in Venue.objects.filter(pk__in=venue_ids):
            q |= Q(location__icontains=v.name)
    if hood_ids:
        hoods = Neighborhood.objects.filter(pk__in=hood_ids)
        for hood in hoods:
            q |= hood.event_q()
    if not q:
        events = Event.objects.none()
    else:
        events = qs.filter(q).distinct().order_by('start_date')[:60]

    from django.conf import settings
    site = getattr(settings, 'SITE_URL', 'https://communityplaylist.com')
    feed = Rss201rev2Feed(
        title=f'Community Playlist — @{handle}\'s feed',
        link=f'{site}/u/@{handle}/',
        description=f'Upcoming Portland events followed by @{handle}',
        language='en',
    )
    for e in events:
        feed.add_item(
            title=e.title,
            link=f'{site}/events/{e.slug}/',
            description=e.description or '',
            pubdate=localtime(e.start_date),
            unique_id=f'{site}/events/{e.slug}/',
        )
    response = HttpResponse(content_type='application/rss+xml; charset=utf-8')
    feed.write(response, 'utf-8')
    return response


# ── Edit suggestions ──────────────────────────────────────────────────────────

@login_required(login_url='/login/')
def suggest_edit(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    import json as _json
    try:
        body = _json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    target_type     = body.get('target_type', '')
    target_id       = body.get('target_id')
    field_name      = body.get('field_name', '')
    suggested_value = body.get('suggested_value', '').strip()
    note            = body.get('note', '').strip()[:500]

    valid_types = {k for k, _ in EditSuggestion.TYPE_CHOICES}
    if target_type not in valid_types:
        return JsonResponse({'error': 'invalid target_type'}, status=400)
    valid_fields = {f for f, _ in EditSuggestion.FIELDS.get(target_type, [])}
    if field_name not in valid_fields:
        return JsonResponse({'error': 'invalid field_name'}, status=400)
    if not suggested_value:
        return JsonResponse({'error': 'suggested_value required'}, status=400)
    try:
        target_id = int(target_id)
    except (TypeError, ValueError):
        return JsonResponse({'error': 'invalid target_id'}, status=400)

    # Grab current value for context
    target = EditSuggestion(target_type=target_type, target_id=target_id).get_target()
    if not target:
        return JsonResponse({'error': 'target not found'}, status=404)
    current_value = str(getattr(target, field_name, '') or '')

    EditSuggestion.objects.create(
        user=request.user,
        target_type=target_type,
        target_id=target_id,
        field_name=field_name,
        current_value=current_value[:2000],
        suggested_value=suggested_value[:5000],
        note=note,
    )
    return JsonResponse({'ok': True})


# ── Admin god-mode dashboard ───────────────────────────────────────────────────

def _fmt_bytes(b):
    if b >= 1_000_000_000: return f'{b / 1_000_000_000:.1f} GB'
    if b >= 1_000_000:     return f'{b / 1_000_000:.1f} MB'
    if b >= 1_000:         return f'{b / 1_000:.1f} KB'
    return f'{b} B'


@staff_member_required
def admin_dashboard(request):
    import os, shutil, glob as _glob
    from django.conf import settings as _settings
    from django.contrib.auth.models import User

    # ── Pending counts ───────────────────────────────────────────
    events_pending    = Event.objects.filter(status='pending').count()
    events_rejected   = Event.objects.filter(status='rejected').count()
    venues_unverified = Venue.objects.filter(verified=False, claimed_by__isnull=False).count()
    suggestions_pend  = EditSuggestion.objects.filter(status='pending').count()

    # ── DB stats ─────────────────────────────────────────────────
    db_path  = str(_settings.DATABASES['default']['NAME'])
    db_size  = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    db_size_s = _fmt_bytes(db_size)

    # ── Event / venue / user counts ──────────────────────────────
    events_total    = Event.objects.count()
    events_approved = Event.objects.filter(status='approved').count()
    events_upcoming = Event.objects.filter(status='approved', start_date__gte=timezone.now()).count()
    venues_total    = Venue.objects.count()
    venues_verified = Venue.objects.filter(verified=True).count()
    users_total     = User.objects.count()
    new_users_7d    = User.objects.filter(date_joined__gte=timezone.now() - timedelta(days=7)).count()

    # ── Media ────────────────────────────────────────────────────
    media_root = str(_settings.MEDIA_ROOT)
    try:
        result = __import__('subprocess').run(['du', '-sb', media_root], capture_output=True, text=True, timeout=5)
        media_bytes = int(result.stdout.split()[0]) if result.returncode == 0 else 0
    except Exception:
        media_bytes = 0
    media_size_s = _fmt_bytes(media_bytes)

    img_exts = ('*.jpg', '*.jpeg', '*.png', '*.webp', '*.JPG', '*.JPEG', '*.PNG')
    img_files = []
    for pat in img_exts:
        img_files.extend(_glob.glob(os.path.join(media_root, '**', pat), recursive=True))
    img_count = len(img_files)
    img_bytes = sum(os.path.getsize(f) for f in img_files if os.path.exists(f))
    img_size_s = _fmt_bytes(img_bytes)

    # ── Disk ─────────────────────────────────────────────────────
    disk = shutil.disk_usage('/')
    disk_used_s  = _fmt_bytes(disk.used)
    disk_free_s  = _fmt_bytes(disk.free)
    disk_total_s = _fmt_bytes(disk.total)
    disk_pct     = int(disk.used / disk.total * 100)

    # ── Memory ───────────────────────────────────────────────────
    try:
        mem = {}
        with open('/proc/meminfo') as _f:
            for _line in _f:
                k, v = _line.split(':', 1)
                mem[k.strip()] = int(v.strip().split()[0]) * 1024
        mem_total = mem.get('MemTotal', 0)
        mem_avail = mem.get('MemAvailable', 0)
        mem_used  = mem_total - mem_avail
        mem_pct   = int(mem_used / mem_total * 100) if mem_total else 0
    except Exception:
        mem_total = mem_used = mem_pct = 0
    mem_used_s  = _fmt_bytes(mem_used)
    mem_total_s = _fmt_bytes(mem_total)

    # ── Load average ─────────────────────────────────────────────
    try:
        with open('/proc/loadavg') as _f:
            load_avg = ' / '.join(_f.read().split()[:3])
    except Exception:
        load_avg = 'N/A'

    # ── Recent pending events ────────────────────────────────────
    recent_pending = Event.objects.filter(status='pending').order_by('-created_at')[:10]
    recent_events  = Event.objects.filter(status='approved').order_by('-created_at')[:8]

    ctx = {
        **admin.site.each_context(request),
        'title': 'God Mode Dashboard',
        # pending
        'events_pending': events_pending,
        'events_rejected': events_rejected,
        'venues_unverified': venues_unverified,
        'suggestions_pend': suggestions_pend,
        'pending_total': events_pending + venues_unverified + suggestions_pend,
        # counts
        'events_total': events_total,
        'events_approved': events_approved,
        'events_upcoming': events_upcoming,
        'venues_total': venues_total,
        'venues_verified': venues_verified,
        'users_total': users_total,
        'new_users_7d': new_users_7d,
        # storage
        'db_size': db_size_s,
        'media_size': media_size_s,
        'img_count': img_count,
        'img_size': img_size_s,
        # system
        'disk_used': disk_used_s,
        'disk_free': disk_free_s,
        'disk_total': disk_total_s,
        'disk_pct': disk_pct,
        'mem_used': mem_used_s,
        'mem_total': mem_total_s,
        'mem_pct': mem_pct,
        'load_avg': load_avg,
        # activity
        'recent_pending': recent_pending,
        'recent_events': recent_events,
        # compress result
        'compress_result': request.session.pop('compress_result', None),
    }
    return TemplateResponse(request, 'admin/dashboard.html', ctx)


@staff_member_required
def admin_compress_images(request):
    """Run image compression and redirect back to dashboard with result."""
    if request.method != 'POST':
        return redirect('/admin/dashboard/')
    import os, glob as _glob
    from PIL import Image as _Image
    from django.conf import settings as _settings

    quality   = int(request.POST.get('quality', 82))
    max_width = int(request.POST.get('max_width', 1920))
    media_root = str(_settings.MEDIA_ROOT)

    saved = 0
    processed = 0
    skipped = 0
    errors = 0

    img_exts = ('*.jpg', '*.jpeg', '*.png', '*.JPG', '*.JPEG', '*.PNG')
    img_files = []
    for pat in img_exts:
        img_files.extend(_glob.glob(os.path.join(media_root, '**', pat), recursive=True))

    for fpath in img_files:
        orig = os.path.getsize(fpath)
        if orig < 40_000:           # already small, skip
            skipped += 1
            continue
        try:
            img = _Image.open(fpath)
            orig_fmt = img.format or 'JPEG'
            # Downscale if wider than max_width
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize((max_width, int(img.height * ratio)), _Image.LANCZOS)
            # Ensure RGB for JPEG
            save_fmt = orig_fmt
            if save_fmt == 'JPEG':
                if img.mode not in ('RGB', 'L'):
                    img = img.convert('RGB')
                img.save(fpath, 'JPEG', quality=quality, optimize=True, progressive=True)
            elif save_fmt == 'PNG':
                img.save(fpath, 'PNG', optimize=True)
            else:
                img.save(fpath, optimize=True, quality=quality)
            new = os.path.getsize(fpath)
            saved += max(0, orig - new)
            processed += 1
        except Exception:
            errors += 1

    result = (
        f'Compressed {processed} images · saved {_fmt_bytes(saved)} · '
        f'skipped {skipped} small · {errors} errors'
    )
    request.session['compress_result'] = result
    return redirect('/admin/dashboard/')
