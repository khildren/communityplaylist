from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.db.models import Prefetch
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import Event, EventPhoto, Genre, SiteStats, CalendarFeed
from .forms import EventSubmitForm, EventPhotoForm, RegisterForm, StyledAuthForm
from .geocode import geocode_location
from urllib.parse import quote
from datetime import timedelta
import requests

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
            'start_date': e.start_date.strftime('%b %d @ %I:%M %p'),
            'flyer_url': e.approved_photos[0].image.url if e.approved_photos else '',
        }
        for e in events_list
        if e.latitude is not None
    ]

    SiteStats.record_visit(request)
    visit_count = f"{SiteStats.get_count():,}"

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
            'start_date': e.start_date.strftime('%b %d @ %I:%M %p'),
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
    start_str = event.start_date.strftime('%Y%m%dT%H%M%S')
    end_dt = event.end_date if event.end_date else event.start_date + timedelta(hours=2)
    end_str = end_dt.strftime('%Y%m%dT%H%M%S')
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

    return render(request, 'events/event_detail.html', {
        'event': event,
        'photos': photos,
        'flyer_photo': flyer_photo,
        'gallery_photos': gallery_photos,
        'photo_form': photo_form,
        'upload_success': upload_success,
        'cal_url': cal_url,
        'maps_url': maps_url,
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
            notify_discord(
                f"🎵 **New event submitted for review!**\n"
                f"**{event.title}**\n"
                f"📅 {event.start_date.strftime('%b %d %Y @ %I:%M %p')}\n"
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


# ── Auth views ──

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
        # Auto-claim any events submitted with this email
        claimed = Event.objects.filter(submitted_email__iexact=email, submitted_user=None)
        claimed.update(submitted_user=user)
        login(request, user)
        messages.success(request, f'Account created! {claimed.count()} event(s) claimed.')
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

    events = Event.objects.filter(submitted_user=request.user).order_by('-created_at')
    feeds  = CalendarFeed.objects.filter(user=request.user)
    return render(request, 'accounts/dashboard.html', {'events': events, 'feeds': feeds})


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

