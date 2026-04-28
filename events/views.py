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
from .models import Event, EventPhoto, Genre, Artist, SiteStats, CalendarFeed, Venue, Neighborhood, UserProfile, Follow, EditSuggestion, PromoterProfile, PlaylistTrack, SavedTrack, TrackReaction, RecordListing, RecordReservation, VideoTrack, Shelter, FlyerBackground, VideoRoomMessage, CommunitySpace, CommunityAsk
from .forms import EventSubmitForm, EventPhotoForm, RegisterForm, StyledAuthForm, VenueForm
from .geocode import geocode_location
from urllib.parse import quote
from datetime import timedelta
import requests
import math
import re

CP_VERSION = '0.9.3'   # bump on each deploy

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
    date_explicitly_set = 'date' in request.GET  # user chose a date filter

    if search_query:
        from django.db.models import Q
        events = events.filter(
            Q(title__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(location__icontains=search_query) |
            Q(genres__name__icontains=search_query)
        ).distinct()

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
    elif search_query and not date_explicitly_set:
        # Search with no explicit date filter → all events, newest first
        events = events.order_by('-start_date')
    else:  # 'future' or default
        events = events.filter(start_date__gte=now)

    # Snapshot before genre filter — genres shown are those available in the
    # current context (date/search/category/neighborhood), never narrowed to zero.
    events_for_genres = events
    if genre_id:
        events = events.filter(genres__id=genre_id)

    neighborhoods = Event.objects.filter(
        status='approved', start_date__gte=now
    ).exclude(neighborhood='').values_list('neighborhood', flat=True).distinct().order_by('neighborhood')

    genres = Genre.objects.filter(
        events__in=events_for_genres
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
    visit_count, daily_count = SiteStats.get_counts()
    visit_count = f"{visit_count:,}"
    daily_count = f"{daily_count:,}"

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

    response = render(request, 'events/event_list.html', {
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
        'daily_count': daily_count,
        'search_query': search_query,
        'search_all_time': bool(search_query and not date_explicitly_set),
        'cp_version': CP_VERSION,
        'banners': banners,
        'happening_now': happening_now,
        'selected_radius': radius,
        'neighborhood_pages': neighborhood_pages,
    })
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    response['Pragma'] = 'no-cache'
    return response


def api_genre_filter(request):
    """Return genres for events matching current filters (excluding genre), for live dropdown updates."""
    from django.db.models import Q
    now = timezone.now()
    events = Event.objects.filter(status='approved')

    q          = request.GET.get('q', '').strip()
    category   = request.GET.get('category', '')
    neighborhood = request.GET.get('neighborhood', '')
    free_only  = request.GET.get('free', '')
    event_type = request.GET.get('event_type', '')
    date_range = request.GET.get('date', 'future')
    date_explicitly_set = 'date' in request.GET

    if q:
        events = events.filter(
            Q(title__icontains=q) |
            Q(description__icontains=q) |
            Q(location__icontains=q) |
            Q(genres__name__icontains=q)
        ).distinct()
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
        today_end   = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        events = events.filter(start_date__gte=today_start, start_date__lte=today_end)
    elif date_range == 'week':
        events = events.filter(start_date__gte=now, start_date__lte=now + timezone.timedelta(days=7))
    elif date_range == 'month':
        events = events.filter(start_date__gte=now, start_date__lte=now + timezone.timedelta(days=30))
    elif date_range == 'past':
        events = events.filter(start_date__lt=now)
    elif q and not date_explicitly_set:
        pass  # all-time search
    else:
        events = events.filter(start_date__gte=now)

    genres = Genre.objects.filter(events__in=events).distinct().order_by('name')
    return JsonResponse({'genres': [{'id': g.id, 'name': g.name} for g in genres]})


def event_archive(request):
    now = timezone.now()
    events = Event.objects.filter(status='approved', start_date__lt=now).order_by('-start_date')

    genre_id = request.GET.get('genre')
    neighborhood = request.GET.get('neighborhood')
    free_only = request.GET.get('free')
    search_query = request.GET.get('q', '').strip()

    if search_query:
        from django.db.models import Q
        events = events.filter(
            Q(title__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(location__icontains=search_query) |
            Q(genres__name__icontains=search_query)
        ).distinct()
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
        'search_query': search_query,
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

    # Guard against DB rows that reference a deleted/missing photo file.
    # Django templates don't catch ValueError, so we compute a safe URL here.
    try:
        photo_url = event.photo.url if event.photo else ''
    except ValueError:
        photo_url = ''
        event.photo = None  # also clear so {% if event.photo %} is False

    can_edit_lineup = request.user.is_authenticated and (
        request.user.is_staff or event.submitted_user == request.user
    )
    can_add_lineup = request.user.is_authenticated

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
        'photo_url': photo_url,
        'venue': venue,
        'event_edit_fields': EditSuggestion.FIELDS['event'],
        'can_edit_lineup': can_edit_lineup,
        'can_add_lineup': can_add_lineup,
        'linked_artists': event.artists.all(),
        'linked_promoters': event.promoters.all(),
    })


_CREW_KEYWORDS = re.compile(
    r'\b(crew|kru|collective|sound|system|records|productions|booking|presents|DJs|music|pdx|posse|squad)\b',
    re.IGNORECASE,
)

def _parse_lineup_from_title(title):
    """
    Splits an event title like "Gnosis DNB with Binsky, The Night Mayor, and the Gnosis Crew"
    into artist and crew name candidates.
    Returns {'artists': [...], 'crews': [...]}
    """
    import re as _re
    # Strip everything before intro keywords
    after = _re.split(r'\bwith|feat(?:uring)?|ft\.?|presents|hosted by\b', title, maxsplit=1, flags=_re.IGNORECASE)
    candidates_str = after[-1].strip() if len(after) > 1 else title

    # Split on commas and " and " / " + "
    parts = _re.split(r',\s*|\s+and\s+|\s*\+\s*', candidates_str)
    # Strip leading articles/conjunctions that bleed through
    parts = [_re.sub(r'^(and\s+the\s+|and\s+|the\s+)', '', p, flags=_re.IGNORECASE).strip().strip('.').strip() for p in parts if p.strip()]

    # Remove articles "the", "a" at the start for classification only (keep full name)
    artists, crews = [], []
    for name in parts:
        if not name:
            continue
        if _CREW_KEYWORDS.search(name):
            crews.append(name)
        else:
            artists.append(name)
    return {'artists': artists, 'crews': crews}


def api_parse_lineup(request):
    """
    GET /api/parse-lineup/?title=...
    Returns parsed artist/crew candidates with DB matches.
    """
    title = request.GET.get('title', '').strip()
    if not title:
        return JsonResponse({'artists': [], 'crews': []})

    parsed = _parse_lineup_from_title(title)

    def find_matches(names, model, label):
        results = []
        for name in names:
            # Search DB for close matches
            qs = model.objects.filter(name__icontains=name.split()[0])  # first word match
            exact = model.objects.filter(name__iexact=name).first()
            results.append({
                'name': name,
                'exact': {'id': exact.pk, 'name': exact.name} if exact else None,
                'suggestions': [{'id': o.pk, 'name': o.name} for o in qs[:4]],
            })
        return results

    return JsonResponse({
        'artists': find_matches(parsed['artists'], Artist, 'artist'),
        'crews':   find_matches(parsed['crews'],   PromoterProfile, 'crew'),
    })


def api_global_search(request):
    """GET /api/search/?q=<query> — search events, artists, and crews for the header search bar."""
    from django.db.models import Q
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'events': [], 'artists': [], 'crews': []})

    events = (
        Event.objects
        .filter(Q(title__icontains=q) | Q(location__icontains=q), status='approved')
        .order_by('-start_date')
        .only('title', 'slug', 'start_date', 'location')[:6]
    )
    artists = (
        Artist.objects
        .filter(name__icontains=q)
        .only('name', 'slug')[:5]
    )
    crews = (
        PromoterProfile.objects
        .filter(Q(name__icontains=q), is_public=True)
        .only('name', 'slug')[:4]
    )

    return JsonResponse({
        'events': [
            {'title': e.title, 'slug': e.slug,
             'date': e.start_date.strftime('%-m/%-d/%y'),
             'loc':  (e.location or '')[:40]}
            for e in events
        ],
        'artists': [{'name': a.name, 'slug': a.slug} for a in artists],
        'crews':   [{'name': p.name, 'slug': p.slug} for p in crews],
    })


def api_route_proxy(request):
    """
    GET /api/route/?from=lat,lng&to=lat,lng
    Server-side OSRM proxy with long-lived cache. Offloads external API calls
    from the browser to Unraid and shares the cache across all users/sessions.
    Returns {pts: [[lat,lng], ...]} or {pts: null} on failure.
    """
    from django.core.cache import cache as _cache
    import re as _re

    coord_re = _re.compile(r'^-?\d+(\.\d+)?,-?\d+(\.\d+)?$')
    frm = request.GET.get('from', '').strip()
    to  = request.GET.get('to', '').strip()
    if not coord_re.match(frm) or not coord_re.match(to):
        return JsonResponse({'pts': None}, status=400)

    cache_key = f'osrm:{frm}:{to}'
    cached = _cache.get(cache_key)
    if cached is not None:
        return JsonResponse({'pts': cached})

    try:
        flat, flng = frm.split(',')
        tlat, tlng = to.split(',')
        url = (f'https://router.project-osrm.org/route/v1/driving/'
               f'{flng},{flat};{tlng},{tlat}?overview=full&geometries=geojson')
        r = requests.get(url, timeout=8)
        d = r.json()
        if d.get('routes'):
            pts = [[c[1], c[0]] for c in d['routes'][0]['geometry']['coordinates']]
        else:
            pts = None
    except Exception:
        pts = None

    _cache.set(cache_key, pts, timeout=86400 * 7)  # cache 7 days
    return JsonResponse({'pts': pts})


def api_artist_lookup(request):
    """
    GET /api/artist-lookup/?q=<name>
    Searches CP DB → MusicBrainz → Last.fm and returns labeled candidates.
    Response: {results: [{source, id?, name, slug?, mb_id?, tags?, image?}]}
    """
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})

    results = []
    seen_names = set()

    # 1. CP database
    for a in Artist.objects.filter(name__icontains=q)[:8]:
        seen_names.add(a.name.lower())
        results.append({
            'source': 'cp', 'id': a.pk, 'name': a.name, 'slug': a.slug,
            'mb_id': a.mb_id, 'city': a.city,
        })

    # 2. MusicBrainz
    try:
        mb_resp = requests.get(
            'https://musicbrainz.org/ws/2/artist',
            params={'query': q, 'fmt': 'json', 'limit': 6},
            headers={'User-Agent': 'CommunityPlaylist/1.0 (hello@communityplaylist.com)'},
            timeout=5,
        )
        for a in mb_resp.json().get('artists', []):
            name = a.get('name', '').strip()
            mb_id = a.get('id', '')
            if not name or name.lower() in seen_names:
                continue
            # Check if already in CP DB under this mb_id
            existing = Artist.objects.filter(mb_id=mb_id).first() if mb_id else None
            if existing:
                if existing.name.lower() not in seen_names:
                    seen_names.add(existing.name.lower())
                    results.append({
                        'source': 'cp', 'id': existing.pk, 'name': existing.name,
                        'slug': existing.slug, 'mb_id': existing.mb_id, 'city': existing.city,
                    })
            else:
                seen_names.add(name.lower())
                area = a.get('area', {})
                tags = [t['name'] for t in a.get('tags', [])[:5]] if a.get('tags') else []
                results.append({
                    'source': 'mb', 'name': name, 'mb_id': mb_id,
                    'city': area.get('name', ''),
                    'tags': tags,
                })
    except Exception:
        pass

    # 3. Last.fm (only if CP+MB returned < 3 results)
    if len(results) < 3:
        try:
            from django.conf import settings as _s
            lfm_key = getattr(_s, 'LASTFM_API_KEY', '')
            if lfm_key:
                lfm_resp = requests.get(
                    'https://ws.audioscrobbler.com/2.0/',
                    params={'method': 'artist.search', 'artist': q, 'api_key': lfm_key,
                            'format': 'json', 'limit': 5},
                    timeout=5,
                )
                matches = lfm_resp.json().get('results', {}).get('artistmatches', {}).get('artist', [])
                for a in matches:
                    name = a.get('name', '').strip()
                    if not name or name.lower() in seen_names:
                        continue
                    seen_names.add(name.lower())
                    results.append({
                        'source': 'lastfm', 'name': name,
                        'image': next((img['#text'] for img in reversed(a.get('image', [])) if img.get('#text')), ''),
                    })
        except Exception:
            pass

    return JsonResponse({'results': results[:12]})


@login_required
def event_lineup_create(request, slug):
    """
    POST {type: 'artist'|'promoter', name, bio?, website?, instagram?, ...} —
    creates or finds an artist/promoter profile, links to event, claims for user.
    Any logged-in user can add; full artist fields accepted for richer profiles.
    Returns {id, name, slug, profile_url, type}.
    """
    event = get_object_or_404(Event, slug=slug)

    import json as _json
    body = _json.loads(request.body)
    obj_type = body.get('type')
    name = body.get('name', '').strip()[:200]
    if not name:
        return JsonResponse({'error': 'name required'}, status=400)

    if obj_type == 'artist':
        obj = Artist.objects.filter(name__iexact=name).first()
        if obj:
            if not obj.claimed_by:
                obj.claimed_by = request.user
                obj.save(update_fields=['claimed_by'])
        else:
            str_field = lambda k, n=200: body.get(k, '').strip()[:n]
            url_field  = lambda k: body.get(k, '').strip()[:500]
            obj = Artist(
                name=name,
                claimed_by=request.user,
                bio=body.get('bio', '').strip()[:4000],
                website=url_field('website'),
                instagram=str_field('instagram', 100),
                soundcloud=str_field('soundcloud', 100),
                bandcamp=url_field('bandcamp'),
                mixcloud=str_field('mixcloud', 100),
                youtube=url_field('youtube'),
                spotify=url_field('spotify'),
                mastodon=url_field('mastodon'),
                bluesky=str_field('bluesky', 100),
                tiktok=str_field('tiktok', 100),
                twitch=str_field('twitch', 100),
                beatport=url_field('beatport'),
                discogs=url_field('discogs'),
                mb_id=str_field('mb_id', 100),
                city=str_field('city', 100),
                is_stub=False,
            )
            obj.save()
        event.artists.add(obj)
        return JsonResponse({'id': obj.pk, 'name': obj.name, 'slug': obj.slug, 'type': 'artist',
                             'profile_url': f'/artists/{obj.slug}/'})

    elif obj_type == 'promoter':
        from django.utils.text import slugify as _slugify
        # Check for existing by name first
        obj = PromoterProfile.objects.filter(name__iexact=name).first()
        if obj:
            if not obj.claimed_by:
                obj.claimed_by = request.user
                obj.save(update_fields=['claimed_by'])
        else:
            base_slug = _slugify(name)
            slug_candidate, i = base_slug, 1
            while PromoterProfile.objects.filter(slug=slug_candidate).exists():
                slug_candidate = f'{base_slug}-{i}'; i += 1
            obj = PromoterProfile.objects.create(
                name=name, slug=slug_candidate, claimed_by=request.user, is_public=True
            )
        event.promoters.add(obj)
        return JsonResponse({'id': obj.pk, 'name': obj.name, 'type': 'promoter',
                             'profile_url': f'/promoters/{obj.slug}/'})

    return JsonResponse({'error': 'bad type'}, status=400)


@login_required
def event_lineup_edit(request, slug):
    """
    POST to add/remove artist or promoter from an event.
    Body: {action: 'add'|'remove', type: 'artist'|'promoter', id: pk}
    Add: any authenticated user. Remove: owner or staff only.
    """
    event = get_object_or_404(Event, slug=slug)
    import json as _json
    body = _json.loads(request.body)
    action = body.get('action')
    if action == 'remove' and not (request.user.is_staff or event.submitted_user == request.user):
        return JsonResponse({'error': 'forbidden'}, status=403)

    obj_type = body.get('type')
    obj_id = int(body.get('id', 0))

    if obj_type == 'artist':
        obj = get_object_or_404(Artist, pk=obj_id)
        if action == 'add':
            event.artists.add(obj)
        else:
            event.artists.remove(obj)
        linked = [{'id': a.pk, 'name': a.name, 'slug': a.slug} for a in event.artists.all()]
    elif obj_type == 'promoter':
        obj = get_object_or_404(PromoterProfile, pk=obj_id)
        if action == 'add':
            event.promoters.add(obj)
        else:
            event.promoters.remove(obj)
        linked = [{'id': p.pk, 'name': p.name, 'slug': p.slug} for p in event.promoters.all()]
    else:
        return JsonResponse({'error': 'bad type'}, status=400)

    return JsonResponse({'ok': True, 'linked': linked})


def event_submit(request):
    if request.method == 'POST':
        form = EventSubmitForm(request.POST, request.FILES)
        if form.is_valid():
            event = form.save(commit=False)
            extra = [u.strip() for u in request.POST.getlist('extra_links') if u.strip()]
            event.extra_links = extra[:10]
            if request.user.is_authenticated:
                event.submitted_user = request.user
            event.save()
            form.save_m2m()
            # Save flyer URL if provided
            flyer_url = request.POST.get('flyer_url', '').strip()
            if flyer_url:
                event.flyer_url = flyer_url
                event.save(update_fields=['flyer_url'])

            # Queue geocoding async — Unraid worker fills lat/lng without blocking this request
            from .models import WorkerTask
            if event.location:
                WorkerTask.objects.create(
                    task_type="geocode_event",
                    payload={"event_id": event.id, "address": event.location},
                )
            # Queue flyer scan async — tokyo7 Ollama enriches missing fields overnight
            if flyer_url:
                WorkerTask.objects.create(
                    task_type="enrich_flyer",
                    payload={"event_id": event.id, "flyer_url": flyer_url},
                )
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
    """Dual-purpose: ?q= returns JSON autocomplete; no query renders the artist list page."""
    q = request.GET.get('q', '')

    # --- JSON autocomplete mode ---
    if q:
        if len(q) < 2:
            return JsonResponse([], safe=False)

        local = list(Artist.objects.filter(name__icontains=q)[:10])
        if local:
            return JsonResponse([{'id': a.id, 'name': a.name, 'slug': a.slug, 'mb_id': a.mb_id} for a in local], safe=False)

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
                if _ is False and not artist.mb_id and mb_id:
                    Artist.objects.filter(pk=artist.pk).update(mb_id=mb_id)
                    artist.mb_id = mb_id
                results.append({'id': artist.id, 'name': artist.name, 'slug': artist.slug, 'mb_id': artist.mb_id})
            return JsonResponse(results, safe=False)
        except Exception:
            return JsonResponse([], safe=False)

    # --- HTML list page mode ---
    # Show any artist with profile content, a claimed account, or at least one linked event.
    # This includes unclaimed stubs created from event imports.
    artists = Artist.objects.filter(
        models.Q(events__isnull=False) |
        models.Q(claimed_by__isnull=False) |
        models.Q(bio__gt='') | models.Q(photo__gt='') | models.Q(instagram__gt='') |
        models.Q(soundcloud__gt='') | models.Q(drive_folder_url__gt='')
    ).distinct().order_by('name')
    return render(request, 'events/artist_list.html', {'artists': artists})


def artist_by_pk(request, pk):
    """Legacy redirect from /artists/<int:pk>/ → /artists/<slug>/."""
    artist = get_object_or_404(Artist, pk=pk)
    return redirect('artist_profile', slug=artist.slug, permanent=True)


def artist_profile(request, slug):
    artist = get_object_or_404(Artist, slug=slug)
    now = timezone.now()

    session_key = f'viewed_artist_{artist.pk}'
    if not request.session.get(session_key):
        try:
            Artist.objects.filter(pk=artist.pk).update(view_count=models.F('view_count') + 1)
            request.session[session_key] = True
            artist.view_count += 1
        except Exception:
            pass  # view count is non-critical; don't 500 on DB contention

    upcoming  = artist.events.filter(status='approved', start_date__gte=now).order_by('start_date')
    past      = artist.events.filter(status='approved', start_date__lt=now).order_by('-start_date')[:20]
    recurring = artist.recurring_events.filter(active=True)
    is_following = (
        request.user.is_authenticated and
        Follow.objects.filter(user=request.user, target_type=Follow.TYPE_ARTIST, target_id=artist.pk).exists()
    )
    # Own tracks (directly linked to this artist's Drive folder)
    own_tracks = list(
        artist.tracks.select_related('genre', 'artist', 'promoter', 'venue')
        .order_by('position', 'title')
    )
    own_pks = {t.pk for t in own_tracks}

    # Cross-posted tracks: any PlaylistTrack where ID3 artist_name matches,
    # uploaded by a different account (crew, venue, other artist)
    tagged_tracks = [
        t for t in PlaylistTrack.objects.filter(
            artist_name__iexact=artist.name
        ).exclude(artist=artist).select_related('genre', 'artist', 'promoter', 'venue')
        if t.pk not in own_pks
    ]
    cross_pks = {t.pk for t in tagged_tracks}

    tracks = own_tracks + tagged_tracks

    can_edit = request.user.is_authenticated and (
        request.user.is_staff or artist.claimed_by == request.user
    )
    saved_ids = set(
        SavedTrack.objects.filter(user=request.user, track_id__in={t.pk for t in tracks}).values_list('track_id', flat=True)
    ) if request.user.is_authenticated and tracks else set()
    yt_embed_html = _get_yt_embed_cached(artist.youtube) if _is_yt_channel(artist.youtube) else ''
    _twitch_data  = _get_twitch_clips_cached(artist.twitch) if artist.twitch and not artist.is_live else {}
    twitch_clips  = _twitch_data.get('clips', [])
    twitch_vods   = _twitch_data.get('vods', [])
    house_mixes_tracks = _get_house_mixes_tracks(artist.house_mixes, sort=artist.house_mixes_sort or 'newest') if artist.house_mixes else []
    return render(request, 'events/artist_profile.html', {
        'artist': artist, 'upcoming': upcoming, 'past': past, 'recurring': recurring,
        'is_following': is_following,
        'artist_edit_fields': EditSuggestion.FIELDS['artist'],
        'tracks': tracks,
        'cross_pks': cross_pks,
        'can_edit': can_edit,
        'saved_ids': saved_ids,
        'yt_embed_html': yt_embed_html,
        'twitch_clips': twitch_clips,
        'twitch_vods': twitch_vods,
        'house_mixes_tracks': house_mixes_tracks,
        'crews': artist.crews.filter(is_public=True).order_by('name'),
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
        return redirect('onboarding')
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
def onboarding_view(request):
    """Post-signup: pick what kind of profiles you want."""
    profile, _ = UserProfile.objects.get_or_create(
        user=request.user,
        defaults={'handle': UserProfile.handle_from_email(request.user.email)},
    )
    if request.method == 'POST':
        profile.wants_artist   = 'wants_artist'   in request.POST
        profile.wants_promoter = 'wants_promoter' in request.POST
        profile.wants_venue    = 'wants_venue'    in request.POST
        profile.onboarded      = True
        profile.save(update_fields=['wants_artist', 'wants_promoter', 'wants_venue', 'onboarded'])
        messages.success(request, "You're all set. Add your profiles from the dashboard.")
        return redirect('dashboard')
    return render(request, 'accounts/onboarding.html', {'profile': profile})


def _build_activity_feed(user, follows):
    """
    Aggregate a community activity feed for the dashboard.
    Returns a list of dicts with keys: type, title, blurb, url, date, source_name.
    Types: board_give | board_iso | ask | event_artist | event_venue | event_space
    """
    from datetime import timedelta
    from django.utils import timezone as _tz
    cutoff = _tz.now() - timedelta(days=60)

    feed = []

    # ── Board offerings (give / ISO / trade) ──────────────────────────────────
    try:
        from board.models import Offering
        for off in Offering.objects.filter(
            created_at__gte=cutoff, is_claimed=False,
        ).order_by('-created_at')[:40]:
            if off.category == Offering.CATEGORY_GIVE:
                ftype = 'board_give'
            elif off.category == Offering.CATEGORY_ISO:
                ftype = 'board_iso'
            else:
                ftype = 'board_trade'
            feed.append({
                'type':        ftype,
                'title':       off.title,
                'blurb':       (off.description or '')[:100],
                'url':         off.get_absolute_url() if hasattr(off, 'get_absolute_url') else '/board/',
                'date':        off.created_at,
                'source_name': off.poster_name or 'Community Board',
            })
    except Exception:
        pass

    # ── Community Asks from any public space/venue ─────────────────────────────
    try:
        for ask in CommunityAsk.objects.filter(
            status='open', created_at__gte=cutoff,
        ).select_related('community_space', 'venue').order_by('-created_at')[:30]:
            source = ask.community_space or ask.venue
            if not source:
                continue
            url = (f'/spaces/{source.slug}/' if ask.community_space
                   else f'/venues/{source.slug}/')
            feed.append({
                'type':        'ask',
                'title':       ask.title,
                'blurb':       (ask.description or '')[:100],
                'url':         url,
                'date':        ask.created_at,
                'source_name': source.name,
            })
    except Exception:
        pass

    # ── Recent events from followed entities ──────────────────────────────────
    follow_map = {(f['follow'].target_type, f['follow'].target_id): f['target'] for f in follows}
    followed_artist_pks  = [tid for (tt, tid) in follow_map if tt == Follow.TYPE_ARTIST]
    followed_venue_pks   = [tid for (tt, tid) in follow_map if tt == Follow.TYPE_VENUE]
    followed_space_pks   = [tid for (tt, tid) in follow_map if tt == Follow.TYPE_SPACE]

    try:
        for ev in Event.objects.filter(
            submitted_artist__pk__in=followed_artist_pks,
            status='approved', start_date__gte=_tz.now().date(),
        ).select_related('submitted_artist')[:20]:
            feed.append({
                'type':        'event_artist',
                'title':       ev.title,
                'blurb':       f'{ev.start_date}',
                'url':         f'/events/{ev.slug}/',
                'date':        ev.created_at,
                'source_name': ev.submitted_artist.name if ev.submitted_artist else '',
            })
    except Exception:
        pass

    try:
        for ev in Event.objects.filter(
            venue__pk__in=followed_venue_pks,
            status='approved', start_date__gte=_tz.now().date(),
        ).select_related('venue')[:20]:
            feed.append({
                'type':        'event_venue',
                'title':       ev.title,
                'blurb':       f'{ev.start_date}',
                'url':         f'/events/{ev.slug}/',
                'date':        ev.created_at,
                'source_name': ev.venue.name if ev.venue else '',
            })
    except Exception:
        pass

    feed.sort(key=lambda x: x['date'], reverse=True)
    return feed[:80]


@login_required(login_url='/login/')
def dashboard(request):
    profile, _ = UserProfile.objects.get_or_create(
        user=request.user,
        defaults={'handle': UserProfile.handle_from_email(request.user.email)},
    )

    # Redirect new users to onboarding (skip param bypasses it)
    if not profile.onboarded:
        if request.GET.get('skip'):
            profile.onboarded = True
            profile.save(update_fields=['onboarded'])
        else:
            return redirect('onboarding')

    # Handle POST actions
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
        elif action == 'save_contact':
            profile.messenger_telegram = request.POST.get('messenger_telegram', '').strip().lstrip('@')[:100]
            profile.messenger_discord  = request.POST.get('messenger_discord', '').strip()[:30]
            profile.messenger_signal   = request.POST.get('messenger_signal', '').strip().lstrip('+')[:100]
            profile.sol_wallet         = request.POST.get('sol_wallet', '').strip()[:120]
            profile.save(update_fields=['messenger_telegram', 'messenger_discord', 'messenger_signal', 'sol_wallet'])
            messages.success(request, 'Contact info saved.')
        elif action == 'toggle_profile_type':
            field = request.POST.get('field', '')
            if field in ('wants_artist', 'wants_promoter', 'wants_venue'):
                setattr(profile, field, not getattr(profile, field))
                profile.save(update_fields=[field])
        elif action == 'release_artist':
            pk = request.POST.get('pk')
            Artist.objects.filter(pk=pk, claimed_by=request.user).update(claimed_by=None)
            messages.success(request, 'Artist claim released.')
        elif action == 'release_promoter':
            pk = request.POST.get('pk')
            PromoterProfile.objects.filter(pk=pk, claimed_by=request.user).update(claimed_by=None)
            messages.success(request, 'Crew claim released.')
        elif action == 'release_venue':
            pk = request.POST.get('pk')
            Venue.objects.filter(pk=pk, claimed_by=request.user).update(claimed_by=None)
            messages.success(request, 'Venue claim released.')
        elif action == 'release_space':
            pk = request.POST.get('pk')
            CommunitySpace.objects.filter(pk=pk, claimed_by=request.user).update(claimed_by=None)
            messages.success(request, 'Space claim released.')
        return redirect('dashboard')

    # Claimed profiles
    claimed_artists   = list(request.user.claimed_artists.all())
    claimed_promoters = list(request.user.claimed_promoters.all())
    claimed_venues    = list(request.user.claimed_venues.all())
    claimed_spaces    = list(CommunitySpace.objects.filter(claimed_by=request.user))

    # If user has claimed profiles, auto-activate the matching flag
    if claimed_artists and not profile.wants_artist:
        profile.wants_artist = True
        profile.save(update_fields=['wants_artist'])
    if claimed_promoters and not profile.wants_promoter:
        profile.wants_promoter = True
        profile.save(update_fields=['wants_promoter'])
    if claimed_venues and not profile.wants_venue:
        profile.wants_venue = True
        profile.save(update_fields=['wants_venue'])

    # Stats
    artist_views   = sum(a.view_count for a in claimed_artists)
    promoter_views = sum(p.view_count for p in claimed_promoters)
    venue_views    = sum(v.view_count for v in claimed_venues)

    events  = Event.objects.filter(submitted_user=request.user).order_by('-created_at')
    feeds   = CalendarFeed.objects.filter(user=request.user)

    follows = Follow.objects.filter(user=request.user)
    follow_data = [{'follow': f, 'target': f.get_target()} for f in follows]
    follow_data = [x for x in follow_data if x['target'] is not None]

    saved_tracks = SavedTrack.objects.filter(user=request.user).select_related(
        'track__genre', 'track__artist', 'track__promoter', 'track__venue'
    ).order_by('-created_at')

    # Outgoing: reservations I made as a buyer
    outgoing_orders = (RecordReservation.objects
                       .filter(buyer=request.user)
                       .select_related('listing__promoter')
                       .order_by('-created_at'))

    # Incoming: reservations on shops I own (promoter claimed by me)
    my_promoter_pks = [p.pk for p in claimed_promoters]
    incoming_orders = (RecordReservation.objects
                       .filter(listing__promoter__pk__in=my_promoter_pks)
                       .select_related('listing__promoter')
                       .order_by('-created_at')) if my_promoter_pks else []

    space_views     = sum(s.view_count for s in claimed_spaces)
    active_profiles = len(claimed_artists) + len(claimed_promoters) + len(claimed_venues) + len(claimed_spaces)

    activity_feed = _build_activity_feed(request.user, follow_data)

    response = render(request, 'accounts/dashboard.html', {
        'profile': profile,
        'events': events,
        'feeds': feeds,
        'follow_data': follow_data,
        'saved_tracks': saved_tracks,
        'outgoing_orders': outgoing_orders,
        'incoming_orders': incoming_orders,
        'claimed_artists': claimed_artists,
        'claimed_promoters': claimed_promoters,
        'claimed_venues': claimed_venues,
        'claimed_spaces': claimed_spaces,
        'artist_views': artist_views,
        'promoter_views': promoter_views,
        'venue_views': venue_views,
        'space_views': space_views,
        'total_views': artist_views + promoter_views + venue_views + space_views,
        'events_pending': events.filter(status='pending').count(),
        'events_approved': events.filter(status='approved').count(),
        'active_profiles':  active_profiles,
        'activity_feed':    activity_feed,
    })
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    response['Pragma'] = 'no-cache'
    return response


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


def rss_feed(request):
    from django.utils.feedgenerator import Rss201rev2Feed

    feed = Rss201rev2Feed(
        title='Community Playlist — PDX Events',
        link='https://communityplaylist.com/',
        description='Portland community events submitted by the people, for the people.',
        language='en',
        feed_url='https://communityplaylist.com/feed/events.rss',
    )

    now = timezone.now()
    events = Event.objects.filter(status='approved', start_date__gte=now).order_by('start_date')[:100]

    category = request.GET.get('category')
    genre_id = request.GET.get('genre')
    free_only = request.GET.get('free')
    if category:
        events = events.filter(category=category)
    if genre_id:
        events = events.filter(genres__id=genre_id)
    if free_only:
        events = events.filter(is_free=True)

    for event in events:
        location = getattr(event, 'location', '') or ''
        description = event.description[:500] if event.description else ''
        if location and not location.startswith(('http', 'www')):
            description = f'{location} — {description}' if description else location

        feed.add_item(
            title=event.title,
            link=f'https://communityplaylist.com/events/{event.slug}/',
            unique_id=f'https://communityplaylist.com/events/{event.slug}/',
            description=description,
            pubdate=event.start_date if timezone.is_aware(event.start_date) else timezone.make_aware(event.start_date),
            categories=[event.get_category_display()] if event.category else [],
        )

    resp = HttpResponse(content_type='application/rss+xml; charset=utf-8')
    feed.write(resp, 'utf-8')
    return resp


def calendar_subscribe(request):
    genres = Genre.objects.filter(
        events__status='approved', events__start_date__gte=timezone.now()
    ).distinct().order_by('name')
    return render(request, 'events/calendar_subscribe.html', {'genres': genres})


def features_page(request):
    return render(request, 'events/features.html')


def credits_page(request):
    return render(request, 'events/credits.html')


@login_required
def save_profile_playlist(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    import json as _json
    try:
        data = _json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    items = data.get('items', [])
    if not items:
        return JsonResponse({'error': 'No items'}, status=400)
    from .models import UserPlaylist
    profile = request.user.profile
    name = f"@{profile.handle}'s Community Playlist"
    pl, created = UserPlaylist.objects.update_or_create(
        user=request.user,
        name=name,
        defaults={'items': items},
    )
    return JsonResponse({'ok': True, 'created': created, 'id': pl.pk, 'name': pl.name, 'count': len(items)})


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
    asks = list(venue.asks.exclude(status='fulfilled'))
    return render(request, 'events/venue_detail.html', {
        'venue': venue,
        'upcoming': upcoming,
        'past': past,
        'now': now,
        'is_following': is_following,
        'venue_edit_fields': EditSuggestion.FIELDS['venue'],
        'asks': asks,
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

    if request.method == 'POST' and request.POST.get('_asks_only') == '1':
        # Asks-only form submitted — rebuild asks without touching VenueForm
        _save_asks_for_venue(venue, request.POST, user=request.user)
        messages.success(request, 'Community Asks saved.')
        return redirect('venue_edit', slug=venue.slug)

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
    asks = list(venue.asks.all())
    return render(request, 'events/venue_register.html', {'form': form, 'venue': venue, 'editing': True, 'asks': asks})


def _parse_asks_from_post(post):
    """Return list of dicts from parallel ask arrays in POST data."""
    titles        = post.getlist('ask_title')
    types         = post.getlist('ask_type')
    descriptions  = post.getlist('ask_description')
    amounts       = post.getlist('ask_target')
    don_urls      = post.getlist('ask_donation_url')
    statuses      = post.getlist('ask_status')
    product_urls  = post.getlist('ask_product_url')
    product_imgs  = post.getlist('ask_product_image_url')
    product_prices = post.getlist('ask_product_price')
    post_to_board = post.getlist('ask_post_to_board')  # value = index str when checked
    valid_types   = [k for k, _ in CommunityAsk.TYPE_CHOICES]
    valid_status  = [k for k, _ in CommunityAsk.STATUS_CHOICES]
    result = []
    for i, title in enumerate(titles):
        title = title.strip()
        if not title:
            continue
        ask_type  = types[i] if i < len(types) and types[i] in valid_types else CommunityAsk.TYPE_ITEM
        status    = statuses[i] if i < len(statuses) and statuses[i] in valid_status else CommunityAsk.STATUS_OPEN
        amount_raw = amounts[i].strip() if i < len(amounts) else ''
        price_raw  = product_prices[i].strip() if i < len(product_prices) else ''
        try:
            amount = int(amount_raw) if amount_raw else None
        except ValueError:
            amount = None
        try:
            price = float(price_raw) if price_raw else None
        except ValueError:
            price = None
        result.append({
            'title':             title,
            'ask_type':          ask_type,
            'description':       descriptions[i].strip() if i < len(descriptions) else '',
            'target_amount':     amount,
            'donation_url':      don_urls[i].strip() if i < len(don_urls) else '',
            'product_url':       product_urls[i].strip() if i < len(product_urls) else '',
            'product_image_url': product_imgs[i].strip() if i < len(product_imgs) else '',
            'product_price':     price,
            'status':            status,
            'post_to_board':     str(i) in post_to_board,
            'sort_order':        i,
        })
    return result


def _create_iso_offering(ask_data, owner_name, neighborhood_name, user, profile_url=''):
    """Create a Buy Nothing ISO Offering for an item ask. Returns the Offering or None."""
    from board.models import Offering
    from django.utils import timezone as _tz
    hood = Neighborhood.objects.filter(name__iexact=neighborhood_name).first() if neighborhood_name else None
    body_parts = []
    if ask_data['description']:
        body_parts.append(ask_data['description'])
    if ask_data['product_url']:
        body_parts.append(f"Product link: {ask_data['product_url']}")
    if profile_url:
        body_parts.append(f"Posted by {owner_name} — {profile_url}")
    offering = Offering.objects.create(
        title=ask_data['title'],
        body='\n'.join(body_parts),
        category=Offering.CATEGORY_ISO,
        neighborhood=hood,
        author_name=owner_name,
        poster_user=user,
        expires_at=_tz.now() + _tz.timedelta(days=180),
        active=True,
    )
    return offering


def _save_asks_for_venue(venue, post, user=None):
    parsed = _parse_asks_from_post(post)
    new_asks = []
    for d in parsed:
        offering = None
        if d['post_to_board'] and d['ask_type'] == CommunityAsk.TYPE_ITEM and user:
            profile_url = f'https://communityplaylist.com/venues/{venue.slug}/'
            offering = _create_iso_offering(d, venue.name, venue.neighborhood, user, profile_url)
        new_asks.append(CommunityAsk(
            venue=venue,
            title=d['title'],
            description=d['description'],
            ask_type=d['ask_type'],
            target_amount=d['target_amount'],
            donation_url=d['donation_url'],
            product_url=d['product_url'],
            product_image_url=d['product_image_url'],
            product_price=d['product_price'],
            board_offering=offering,
            status=d['status'],
            sort_order=d['sort_order'],
        ))
    CommunityAsk.objects.filter(venue=venue).delete()
    CommunityAsk.objects.bulk_create(new_asks)


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

    session_key = f'viewed_neighborhood_{hood.pk}'
    if not request.session.get(session_key):
        Neighborhood.objects.filter(pk=hood.pk).update(view_count=models.F('view_count') + 1)
        request.session[session_key] = True
        hood.view_count += 1

    upcoming = Event.objects.filter(
        status='approved',
        start_date__gte=now,
    ).filter(hood.event_q()).order_by('start_date')[:30]

    # Board topics tagged to this neighborhood
    topics = Topic.objects.filter(neighborhood=hood).order_by('-pinned', '-created_at')[:20]

    # Free & Trade offerings for this neighborhood
    from board.models import Offering
    offerings = Offering.objects.filter(
        neighborhood=hood, active=True, is_claimed=False, expires_at__gt=now,
    ).order_by('-created_at')[:12]

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
        'offerings': offerings,
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
            profile.is_public              = bool(request.POST.get('is_public'))
            profile.lastfm_username        = request.POST.get('lastfm_username', '').strip()[:100]
            profile.listenbrainz_username  = request.POST.get('listenbrainz_username', '').strip()[:100]
            profile.discogs_username       = request.POST.get('discogs_username', '').strip()[:100]
            profile.show_embeds          = bool(request.POST.get('show_embeds'))
            profile.show_rss_feed        = bool(request.POST.get('show_rss_feed'))
            profile.show_following       = bool(request.POST.get('show_following'))
            profile.show_saved_tracks    = bool(request.POST.get('show_saved_tracks'))
            profile.show_upcoming_events = bool(request.POST.get('show_upcoming_events'))
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


# ── YouTube channel embed cache (in-memory, 1-hour TTL) ──────────────────────
import time as _time
_yt_embed_cache: dict = {}
_YT_EMBED_TTL = 3600  # seconds

def _get_yt_embed_cached(url):
    """Return cached YouTube channel embed HTML, fetching if stale/missing."""
    now = _time.time()
    entry = _yt_embed_cache.get(url)
    if entry and now - entry[1] < _YT_EMBED_TTL:
        return entry[0]
    html = _fetch_embed_html(url) or ''
    _yt_embed_cache[url] = (html, now)
    return html

def _is_yt_channel(url):
    if not url or 'youtube.com' not in url:
        return False
    # Modern formats: /@handle, /channel/UCxxx, /c/name
    if any(p in url for p in ('/@', '/channel/UC', '/c/')):
        return True
    # Legacy /username format — youtube.com/username with no other path segments
    import re as _re_yt
    return bool(_re_yt.search(r'youtube\.com/([A-Za-z0-9_]+)/?$', url))


# ── Twitch clips helper ───────────────────────────────────────────────────────
_twitch_clips_cache: dict = {}
_TWITCH_CLIPS_TTL = 3600

# ── House-Mixes.com ───────────────────────────────────────────────────────────
_HM_CACHE: dict = {}
_HM_TTL = 900  # 15 min


def _get_house_mixes_tracks(username, sort='newest', limit=12):
    """Fetch mix list for a house-mixes.com username via RSC payload. Cached 15 min.
    sort: 'newest' | 'oldest' | 'downloads' | 'plays'
    """
    import re, time, json as _json
    if not username:
        return []
    cache_key = username
    cached = _HM_CACHE.get(cache_key)
    if cached and time.time() - cached['ts'] < _HM_TTL:
        raw = cached['raw']
    else:
        try:
            r = requests.get(
                f'https://www.house-mixes.com/{username}',
                headers={'User-Agent': 'Mozilla/5.0 Chrome/120.0.0.0', 'RSC': '1'},
                timeout=8,
            )
            if r.status_code != 200:
                return []
            data = r.text
            # Parse initialMixes array which contains full track objects
            m = re.search(r'"initialMixes":\[(.+?)\],"initialPag', data, re.S)
            if m:
                try:
                    raw = _json.loads('[' + m.group(1) + ']')
                except Exception:
                    raw = []
            else:
                raw = []
            # Fallback: build minimal records from regex if JSON parse failed
            if not raw:
                uuids = re.findall(
                    rf'/{re.escape(username)}/([0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}})(?!\w)', data)
                META = {username, 'House-Mixes.com', 'viewport', 'description', 'keywords',
                        'robots', 'msapplication-TileColor', 'msapplication-config'}
                names = [n for n in re.findall(r'"name":"([^"]+)"', data) if n not in META]
                artworks = re.findall(
                    r'https://ik\.imagekit\.io/housemixes/tr:n-athumb7/[^"]+artwork[^"]+\.jpg', data)
                raw = [{'name': names[i] if i < len(names) else f'Mix {i+1}',
                        'waveformUrl': f'https://files.house-mixes.com/mp3/{username}/{uuid}.mp3',
                        'artwork': artworks[i] if i < len(artworks) else '',
                        'dateAdded': '', 'totalDownloads': 0, 'totalPlays': 0}
                       for i, uuid in enumerate(uuids)]
            _HM_CACHE[cache_key] = {'ts': time.time(), 'raw': raw}
        except Exception:
            return []

    # Sort
    if sort == 'oldest':
        raw = sorted(raw, key=lambda x: x.get('dateAdded') or '', reverse=False)
    elif sort == 'downloads':
        raw = sorted(raw, key=lambda x: x.get('totalDownloads') or 0, reverse=True)
    elif sort == 'plays':
        raw = sorted(raw, key=lambda x: x.get('totalPlays') or 0, reverse=True)
    # 'newest' = default order from API (already newest-first)

    tracks = []
    for mix in raw[:limit]:
        # waveformUrl is like https://files.house-mixes.com/mp3/user/uuid.mp3 — reuse as stream_url
        waveform = mix.get('waveformUrl', '')
        uuid_m = re.search(
            rf'/{re.escape(username)}/([0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}})',
            waveform)
        if not uuid_m:
            continue
        uuid = uuid_m.group(1)
        artwork = (mix.get('artworkUrl') or mix.get('artwork') or
                   mix.get('coverUrl') or mix.get('coverImageUrl') or '')
        tracks.append({
            'title':      mix.get('name') or mix.get('title') or f'Mix',
            'stream_url': f'https://files.house-mixes.com/mp3/{username}/{uuid}.mp3',
            'thumbnail':  artwork,
            'source_url': f'https://www.house-mixes.com/{username}',
            'downloads':  mix.get('totalDownloads', 0),
            'plays':      mix.get('totalPlays', 0),
        })
    return tracks


_TWITCH_EMPTY = {'clips': [], 'vods': []}

def _get_twitch_clips_cached(channel):
    """Return {'clips': [...], 'vods': [...]} for a Twitch channel, cached 1h."""
    if not channel:
        return _TWITCH_EMPTY
    now = _time.time()
    entry = _twitch_clips_cache.get(channel)
    if entry and now - entry[1] < _TWITCH_CLIPS_TTL:
        return entry[0]
    data = _fetch_twitch_clips(channel)
    _twitch_clips_cache[channel] = (data, now)
    return data

def _fetch_twitch_clips(channel):
    """Return up to 4 clips for a channel; falls back to past VODs if no clips."""
    from django.conf import settings as _s
    cid = getattr(_s, 'TWITCH_CLIENT_ID', '')
    csec = getattr(_s, 'TWITCH_CLIENT_SECRET', '')
    _empty = {'clips': [], 'vods': []}
    if not cid or not csec:
        return _empty
    try:
        tok_r = requests.post(
            'https://id.twitch.tv/oauth2/token',
            params={'client_id': cid, 'client_secret': csec, 'grant_type': 'client_credentials'},
            timeout=5,
        )
        token = tok_r.json().get('access_token', '')
        if not token:
            return _empty
        hdrs = {'Client-ID': cid, 'Authorization': f'Bearer {token}'}
        user_r = requests.get(
            'https://api.twitch.tv/helix/users',
            params={'login': channel}, headers=hdrs, timeout=5,
        )
        users = user_r.json().get('data', [])
        if not users:
            return _empty
        broadcaster_id = users[0]['id']

        import re as _re

        def _parse_dur(dur_str):
            dur = 0
            for unit, mult in [('h', 3600), ('m', 60), ('s', 1)]:
                m = _re.search(r'(\d+)' + unit, dur_str or '0s')
                if m:
                    dur += int(m.group(1)) * mult
            return dur

        # Top clips
        clips_r = requests.get(
            'https://api.twitch.tv/helix/clips',
            params={'broadcaster_id': broadcaster_id, 'first': 4},
            headers=hdrs, timeout=5,
        )
        clips = []
        for c in clips_r.json().get('data', []):
            clips.append({
                'id':        c['id'],
                'title':     c['title'],
                'thumbnail': c['thumbnail_url'],
                'views':     c['view_count'],
                'duration':  int(c.get('duration', 0)),
                'url':       c['url'],
                'type':      'clip',
            })

        # Past VODs / archives (always fetch — shown as separate section)
        vods_r = requests.get(
            'https://api.twitch.tv/helix/videos',
            params={'user_id': broadcaster_id, 'first': 4, 'type': 'archive'},
            headers=hdrs, timeout=5,
        )
        vods = []
        for v in vods_r.json().get('data', []):
            thumb = v.get('thumbnail_url', '').replace('%{width}', '320').replace('%{height}', '180')
            vods.append({
                'id':        v['id'],
                'title':     v['title'],
                'thumbnail': thumb,
                'views':     v.get('view_count', 0),
                'duration':  _parse_dur(v.get('duration', '0s')),
                'url':       v['url'],
                'type':      'vod',
            })
        return {'clips': clips, 'vods': vods}
    except Exception:
        return _empty


# ── Discogs API helper ────────────────────────────────────────────────────────
_discogs_cache: dict = {}
_DISCOGS_TTL = 86400  # 24h — release metadata doesn't change often

def _discogs_search(artist, title):
    """Search Discogs for a release, return {'cover_url', 'label', 'year', 'discogs_id'} or {}."""
    key = f'{artist.lower()}|{title.lower()}'
    now = _time.time()
    entry = _discogs_cache.get(key)
    if entry and now - entry[1] < _DISCOGS_TTL:
        return entry[0]

    try:
        resp = requests.get(
            'https://api.discogs.com/database/search',
            params={'q': f'{artist} {title}', 'type': 'release', 'per_page': 1},
            headers={
                'User-Agent': 'CommunityPlaylist/1.0 +https://communityplaylist.com',
                'Accept': 'application/json',
            },
            timeout=8,
        )
        resp.raise_for_status()
        results = resp.json().get('results', [])
        if not results:
            _discogs_cache[key] = ({}, now)
            return {}
        r = results[0]
        data = {
            'cover_url':   r.get('cover_image', '') or r.get('thumb', ''),
            'label':       (r.get('label') or [''])[0] if r.get('label') else '',
            'year':        str(r.get('year', '')),
            'discogs_id':  str(r.get('id', '')),
            'preview_url': '',  # search results don't include videos; fetched by URL only
        }
        _discogs_cache[key] = (data, now)
        return data
    except Exception:
        _discogs_cache[key] = ({}, now)
        return {}


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

    saved_tracks = SavedTrack.objects.filter(user=profile.user).select_related(
        'track__genre', 'track__artist', 'track__promoter', 'track__venue'
    ).order_by('-created_at') if profile.show_saved_tracks else []
    public_follow_data   = follow_data if profile.show_following else []
    public_events        = events if profile.show_upcoming_events else []
    return render(request, 'accounts/public_profile.html', {
        'profile': profile,
        'events': public_events,
        'follow_data': public_follow_data,
        'oembed_embeds': oembed_embeds,
        'saved_tracks': saved_tracks,
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
    valid_types = {Follow.TYPE_ARTIST, Follow.TYPE_VENUE, Follow.TYPE_NEIGHBORHOOD, Follow.TYPE_PROMOTER, Follow.TYPE_SPACE}
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
    from django.utils.feedgenerator import Rss201rev2Feed, Enclosure
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


# ── Drive sync helpers ──────────────────────────────────────────────────────────

@login_required
def artist_edit(request, slug):
    artist = get_object_or_404(Artist, slug=slug)
    if not (request.user.is_staff or artist.claimed_by == request.user):
        return redirect('artist_profile', slug=artist.slug)

    SOCIAL_FIELDS = ['instagram', 'soundcloud', 'bandcamp', 'mixcloud', 'youtube',
                     'spotify', 'mastodon', 'bluesky', 'kofi', 'tiktok', 'twitch',
                     'house_mixes']

    if request.method == 'GET':
        return render(request, 'events/artist_edit.html', {'artist': artist})

    import re as _re
    old_drive = artist.drive_folder_url or ''
    # brand_color — validate hex before saving
    bc = request.POST.get('brand_color', '').strip()
    if _re.fullmatch(r'#[0-9a-fA-F]{6}', bc):
        artist.brand_color = bc.lower()
    elif not bc:
        artist.brand_color = ''
    for field in ['bio', 'website', 'drive_folder_url'] + SOCIAL_FIELDS:
        val = request.POST.get(field, '').strip()
        setattr(artist, field, val)
    sort = request.POST.get('house_mixes_sort', '').strip()
    if sort in ('newest', 'oldest', 'downloads', 'plays'):
        artist.house_mixes_sort = sort
    if request.FILES.get('photo'):
        artist.photo = request.FILES['photo']
    # MB ID — accept a UUID or a full musicbrainz.org/artist/<uuid> URL; empty = clear
    mb_raw = request.POST.get('mb_id', '').strip()
    if mb_raw:
        _uuid_match = _re.search(
            r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})',
            mb_raw, _re.I
        )
        artist.mb_id = _uuid_match.group(1).lower() if _uuid_match else artist.mb_id
    else:
        artist.mb_id = ''
    artist.save()
    if old_drive and not artist.drive_folder_url:
        PlaylistTrack.objects.filter(artist=artist).delete()
    messages.success(request, 'Profile updated.')
    return redirect('artist_edit', slug=artist.slug)


@login_required
def artist_register(request):
    """Direct artist profile creation — no event claim required."""
    SOCIAL_FIELDS = ['instagram', 'soundcloud', 'bandcamp', 'mixcloud', 'youtube',
                     'spotify', 'mastodon', 'bluesky', 'tiktok', 'twitch']

    if request.method == 'GET':
        return render(request, 'events/artist_register.html', {})

    name             = request.POST.get('name', '').strip()[:200]
    bio              = request.POST.get('bio', '').strip()
    website          = request.POST.get('website', '').strip()
    drive_folder_url = request.POST.get('drive_folder_url', '').strip()
    photo            = request.FILES.get('photo')

    errors = {}
    if not name:
        errors['name'] = 'Artist name is required.'
    elif Artist.objects.filter(name__iexact=name).exists():
        existing = Artist.objects.get(name__iexact=name)
        if existing.claimed_by:
            errors['name'] = f'"{name}" already has an owner. Contact us if you think this is your profile.'
        else:
            # Unclaimed stub — let them claim it and fill in the details
            existing.claimed_by = request.user
            existing.bio = bio or existing.bio
            existing.website = website or existing.website
            existing.drive_folder_url = drive_folder_url or existing.drive_folder_url
            for f in SOCIAL_FIELDS:
                val = request.POST.get(f, '').strip()
                if val:
                    setattr(existing, f, val)
            if photo:
                existing.photo = photo
            existing.save()
            messages.success(request, f'Claimed existing profile for {existing.name}.')
            return redirect('artist_profile', slug=existing.slug)

    if errors:
        return render(request, 'events/artist_register.html', {'errors': errors, 'prev': request.POST})

    artist = Artist(name=name, bio=bio, website=website,
                    drive_folder_url=drive_folder_url, claimed_by=request.user)
    for f in SOCIAL_FIELDS:
        val = request.POST.get(f, '').strip()
        if val:
            setattr(artist, f, val)
    if photo:
        artist.photo = photo
    artist.save()
    messages.success(request, f'Artist profile created for {artist.name}.')
    return redirect('artist_profile', slug=artist.slug)


import re as _re

_DRIVE_FOLDER_RE = _re.compile(r'/folders/([A-Za-z0-9_-]+)')
_TRACK_NAME_RE   = _re.compile(
    r'^(?P<artist>.+?)\s*-\s*(?P<title>.+?)'
    r'(?:\s*\[(?P<genre>[^\]]+)\])?'
    r'(?:\s*@\s*(?P<venue>.+?))?'
    r'(?:\s+(?P<date>\d{4}-\d{2}-\d{2}))?'
    r'\.\w+$'
)


def _extract_folder_id(url):
    m = _DRIVE_FOLDER_RE.search(url)
    return m.group(1) if m else None


def _parse_track_name(filename):
    """Parse 'Artist - Title [genre] @ Venue 2024-01-15.mp3' → dict."""
    m = _TRACK_NAME_RE.match(filename.strip())
    if not m:
        # Fallback: strip extension, use as title
        title = _re.sub(r'\.\w+$', '', filename).strip()
        return {'title': title, 'artist_name': '', 'genre_raw': '', 'recorded_at': '', 'recorded_date': None}
    from datetime import date
    raw_date = m.group('date')
    try:
        parsed_date = date.fromisoformat(raw_date) if raw_date else None
    except ValueError:
        parsed_date = None
    return {
        'title':       m.group('title').strip(),
        'artist_name': m.group('artist').strip(),
        'genre_raw':   (m.group('genre') or '').strip(),
        'recorded_at': (m.group('venue') or '').strip(),
        'recorded_date': parsed_date,
    }


def _read_id3_from_stream(stream_url, api_key):
    """
    Fetch the first 256 KB of an MP3 stream and parse ID3 tags with mutagen.
    Returns a dict with keys: title, artist, genre, date, duration_secs (all may be empty/None).
    """
    try:
        from mutagen.id3 import ID3NoHeaderError
        from mutagen.mp3 import MP3
        import io
        chunk_resp = requests.get(
            stream_url,
            headers={
                'Range': 'bytes=0-131071',
                'User-Agent': 'Mozilla/5.0 (compatible; CommunityPlaylist/1.0)',
            },
            timeout=6,
        )
        data = chunk_resp.content
        buf = io.BytesIO(data)

        # Try mutagen MP3 (reads ID3 + Xing/LAME header for duration estimate)
        buf.seek(0)
        try:
            mp3 = MP3(buf)
            tags   = mp3.tags or {}
            def tag(key):
                v = tags.get(key)
                return str(v.text[0]).strip() if v and hasattr(v, 'text') and v.text else ''
            title    = tag('TIT2')
            artist   = tag('TPE1') or tag('TPE2')
            genre    = tag('TCON')
            date_raw = tag('TDRC') or tag('TYER') or tag('TDRL')
            duration = int(mp3.info.length) if mp3.info and mp3.info.length else None
        except Exception:
            title = artist = genre = date_raw = ''
            duration = None

        # Parse year/date out of TDRC which can be "2024-01-15" or just "2024"
        from datetime import date as _date
        parsed_date = None
        if date_raw:
            d = str(date_raw).strip()
            for fmt in ('%Y-%m-%d', '%Y-%m', '%Y'):
                try:
                    import datetime
                    parsed_date = datetime.datetime.strptime(d[:len(fmt)], fmt).date()
                    break
                except ValueError:
                    continue

        # Strip brackets from TCON if present (e.g. "(31)" → "Trance")
        if genre and genre.startswith('(') and genre.endswith(')'):
            genre = ''  # numeric genre codes — skip, too ugly

        return {
            'title':        title,
            'artist':       artist,
            'genre':        genre,
            'recorded_date': parsed_date,
            'duration_secs': duration,
        }
    except Exception:
        return {'title': '', 'artist': '', 'genre': '', 'recorded_date': None, 'duration_secs': None}


def _list_drive_audio(folder_id, api_key, max_depth=3, _depth=0):
    """
    Recursively list all MP3 files in a Drive folder up to max_depth levels deep.
    Returns a flat list of Drive file dicts (id, name, mimeType, size, createdTime).
    """
    _HDR = {'User-Agent': 'CommunityPlaylist/1.0'}
    files = []

    # ── audio files in this folder ────────────────────────────────────────
    page_token = None
    while True:
        url = (
            f'https://www.googleapis.com/drive/v3/files'
            f'?q=%27{folder_id}%27+in+parents'
            f'+and+mimeType+%3D+%27audio%2Fmpeg%27'
            f'+and+trashed+%3D+false'
            f'&orderBy=name'
            f'&fields=files(id,name,mimeType,size,createdTime),nextPageToken'
            f'&key={api_key}&pageSize=100'
        )
        if page_token:
            from urllib.parse import quote as _q
            url += f'&pageToken={_q(page_token)}'
        resp = requests.get(url, timeout=15, headers=_HDR)
        resp.raise_for_status()
        data = resp.json()
        files.extend(data.get('files', []))
        page_token = data.get('nextPageToken')
        if not page_token:
            break

    # ── recurse into sub-folders ──────────────────────────────────────────
    if _depth < max_depth:
        sub_url = (
            f'https://www.googleapis.com/drive/v3/files'
            f'?q=%27{folder_id}%27+in+parents'
            f'+and+mimeType+%3D+%27application%2Fvnd.google-apps.folder%27'
            f'+and+trashed+%3D+false'
            f'&orderBy=name'
            f'&fields=files(id,name)'
            f'&key={api_key}&pageSize=100'
        )
        sub_resp = requests.get(sub_url, timeout=15, headers=_HDR)
        if sub_resp.ok:
            for sub in sub_resp.json().get('files', []):
                files.extend(
                    _list_drive_audio(sub['id'], api_key, max_depth, _depth + 1)
                )

    return files


def _sync_drive_folder(source_type, source_obj):
    """
    Fetch files from a public Google Drive folder (recursively) and upsert
    PlaylistTrack records.  Deletes tracks whose files were removed from Drive.
    source_type: 'artist' | 'promoter' | 'venue'
    Returns (added, updated, deleted, error_string).
    """
    from django.conf import settings as _s
    api_key = getattr(_s, 'GOOGLE_DRIVE_API_KEY', '')
    if not api_key:
        return 0, 0, 0, 'GOOGLE_DRIVE_API_KEY not set in settings'

    folder_url = getattr(source_obj, 'drive_folder_url', '') or ''
    folder_id = _extract_folder_id(folder_url)
    if not folder_id:
        return 0, 0, 0, 'Invalid or missing Drive folder URL'

    try:
        files = _list_drive_audio(folder_id, api_key, max_depth=3)
    except Exception as exc:
        return 0, 0, 0, str(exc)
    added = updated = deleted = 0

    incoming_ids = [f['id'] for f in files]

    # Delete tracks from this source that no longer exist in the Drive folder
    source_filter = {source_type: source_obj}
    removed = PlaylistTrack.objects.filter(**source_filter).exclude(
        drive_file_id__in=incoming_ids
    )
    deleted, _ = removed.delete()

    # Pre-fetch existing file IDs so we only read ID3 for genuinely new files
    existing_ids = set(
        PlaylistTrack.objects.filter(drive_file_id__in=incoming_ids)
        .values_list('drive_file_id', flat=True)
    )

    for pos, f in enumerate(files):
        file_id   = f['id']
        filename  = f['name']
        mime_type = f.get('mimeType', '')
        # Player URL: API key format — browser can stream this (supports Range/seeking)
        stream_url = f'https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&key={api_key}'
        # ID3 read URL: uc format — works server-side without browser cookies
        id3_url = f'https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t'
        is_new = file_id not in existing_ids

        # Parse Drive file creation date as last-resort fallback
        drive_date = None
        raw_created = f.get('createdTime', '')
        if raw_created:
            try:
                from datetime import date as _date
                drive_date = _date.fromisoformat(raw_created[:10])
            except Exception:
                pass

        fn = _parse_track_name(filename)

        if is_new:
            # Only hit the Drive API for ID3 on new tracks — existing ones keep their metadata
            id3 = _read_id3_from_stream(id3_url, api_key)
            title        = id3['title']        or fn['title']        or filename
            artist_name  = id3['artist']       or fn['artist_name']
            genre_raw    = id3['genre']        or fn['genre_raw']
            recorded_date = id3['recorded_date'] or fn['recorded_date'] or drive_date
            duration_secs = id3['duration_secs']
        else:
            # Existing track — only refresh position and stream_url, keep stored metadata
            PlaylistTrack.objects.filter(drive_file_id=file_id).update(
                position=pos, stream_url=stream_url
            )
            updated += 1
            continue

        recorded_at = fn['recorded_at']

        # Resolve genre FK
        genre_obj = None
        if genre_raw:
            genre_obj, _ = Genre.objects.get_or_create(
                name__iexact=genre_raw,
                defaults={'name': genre_raw.title()},
            )

        defaults = {
            'title':         title,
            'artist_name':   artist_name,
            'genre':         genre_obj,
            'genre_raw':     genre_raw,
            'recorded_at':   recorded_at,
            'recorded_date': recorded_date,
            'duration_secs': duration_secs,
            'stream_url':    stream_url,
            'mime_type':     mime_type,
            'position':      pos,
        }
        if source_type == 'artist':
            defaults['artist'] = source_obj
        elif source_type == 'promoter':
            defaults['promoter'] = source_obj
        elif source_type == 'venue':
            defaults['venue'] = source_obj

        _, created = PlaylistTrack.objects.update_or_create(
            drive_file_id=file_id,
            defaults=defaults,
        )
        if created:
            added += 1
        else:
            updated += 1

    return added, updated, deleted, None


# ── Drive sync endpoint (HTMX POST) ────────────────────────────────────────────

@login_required
def drive_sync(request):
    if request.method != 'POST':
        return HttpResponse(status=405)

    source_type = request.POST.get('source_type')  # artist | promoter | venue
    source_id   = request.POST.get('source_id')

    if source_type == 'artist':
        obj = get_object_or_404(Artist, pk=source_id)
        can = request.user.is_staff or obj.claimed_by == request.user
    elif source_type == 'promoter':
        obj = get_object_or_404(PromoterProfile, pk=source_id)
        can = request.user.is_staff or obj.claimed_by == request.user
    elif source_type == 'venue':
        obj = get_object_or_404(Venue, pk=source_id)
        can = request.user.is_staff or obj.claimed_by == request.user
    else:
        return HttpResponse('<p class="sync-error">Unknown source type.</p>')

    if not can:
        return HttpResponse('<p class="sync-error">Not authorised.</p>')

    added, updated, deleted, err = _sync_drive_folder(source_type, obj)
    if err:
        return HttpResponse(f'<p class="sync-error">Sync failed: {err}</p>')

    parts = []
    if added:   parts.append(f'{added} added')
    if deleted: parts.append(f'{deleted} removed')
    if updated: parts.append(f'{updated} updated')
    summary = ', '.join(parts) if parts else 'no changes'
    return HttpResponse(f'<p class="sync-ok" data-reload="1">✓ {summary}</p>')


@login_required
def delete_track(request, pk):
    """POST — delete a PlaylistTrack. Only the owner (claimed artist/promoter/venue) or staff."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    track = get_object_or_404(PlaylistTrack, pk=pk)
    user = request.user
    can = user.is_staff
    if not can:
        if track.artist and track.artist.claimed_by == user:
            can = True
        elif track.promoter and track.promoter.claimed_by == user:
            can = True
        elif track.venue and track.venue.claimed_by == user:
            can = True
    if not can:
        return JsonResponse({'error': 'forbidden'}, status=403)
    track.delete()
    return JsonResponse({'ok': True})


# ── Playlist tracks JSON (for the global music player) ─────────────────────────

def playlist_tracks_json(request):
    """
    Returns JSON list of all PlaylistTrack records with stream URLs.
    Optional ?genre=<name> filter.
    Used by the CP music player in the header.
    """
    genre_filter = request.GET.get('genre', '').strip()
    qs = PlaylistTrack.objects.select_related('genre', 'artist', 'promoter', 'venue')
    if genre_filter:
        qs = qs.filter(genre__name__iexact=genre_filter)
    def source_url(t):
        if t.artist:
            return f'/artists/{t.artist.slug}/'
        if t.promoter:
            return f'/promoters/{t.promoter.slug}/'
        if t.venue:
            return f'/venues/{t.venue.slug}/'
        return ''

    tracks = [
        {
            'id':          t.pk,
            'title':       t.title,
            'artist':      t.artist_name or t.source_label,
            'genre':       t.genre.name if t.genre else t.genre_raw,
            'recorded_at': t.recorded_at,
            'stream_url':  t.stream_url,
            'source':      t.source_label,
            'source_url':  source_url(t),
            'art_url':     t.artist.photo.url if (t.artist and t.artist.photo) else '',
        }
        for t in qs.order_by('-pk')  # newest first
    ]

    # Merge house-mixes.com tracks (no genre metadata — only in ALL channel)
    if not genre_filter:
        hm_artists = Artist.objects.filter(house_mixes__gt='', is_stub=False).values_list(
            'name', 'house_mixes', 'house_mixes_sort', 'slug')
        for a_name, hm_user, hm_sort, a_slug in hm_artists:
            for hm in _get_house_mixes_tracks(hm_user, sort=hm_sort or 'newest'):
                tracks.append({
                    'id':          None,
                    'title':       hm['title'],
                    'artist':      a_name,
                    'genre':       None,
                    'recorded_at': None,
                    'stream_url':  hm['stream_url'],
                    'source':      'House-Mixes',
                    'source_url':  f'/artists/{a_slug}/',
                    'art_url':     hm.get('thumbnail', ''),
                })

    genres = list(
        Genre.objects.filter(tracks__isnull=False)
        .values_list('name', flat=True)
        .distinct()
        .order_by('name')
    )
    return JsonResponse({'tracks': tracks, 'genres': genres})


def api_queue(request):
    """
    Unified playback queue: audio tracks + YouTube/Twitch VODs, interleaved.

    ?genre=X  → audio tracks for that genre only (video stays in ALL)
    no param  → all audio + non-live video, 1 video every 8 audio tracks
    Live Twitch streams are excluded from the queue and returned in live_now[].
    """
    import random
    from datetime import timedelta

    genre_filter = request.GET.get('genre', '').strip().lower()
    if genre_filter in ('all', ''):
        genre_filter = ''

    # ── Audio tracks ──────────────────────────────────────────────────────────
    qs = PlaylistTrack.objects.select_related('genre', 'artist', 'promoter', 'venue')
    if genre_filter:
        qs = qs.filter(genre__name__iexact=genre_filter)

    def _track_source_url(t):
        if t.artist:   return f'/artists/{t.artist.slug}/'
        if t.promoter: return f'/promoters/{t.promoter.slug}/'
        if t.venue:    return f'/venues/{t.venue.slug}/'
        return ''

    audio_tracks = [
        {
            'type':       'audio',
            'id':         t.pk,
            'title':      t.title,
            'artist':     t.artist_name or t.source_label,
            'genre':      t.genre.name if t.genre else (t.genre_raw or ''),
            'stream_url': t.stream_url,
            'source_url': _track_source_url(t),
            'art_url':    t.artist.photo.url if (t.artist and t.artist.photo) else '',
        }
        for t in qs.order_by('-pk')
    ]

    # Merge house-mixes (ALL only, no genre metadata)
    if not genre_filter:
        hm_artists = Artist.objects.filter(house_mixes__gt='', is_stub=False).values_list(
            'name', 'house_mixes', 'house_mixes_sort', 'slug')
        for a_name, hm_user, hm_sort, a_slug in hm_artists:
            for hm in _get_house_mixes_tracks(hm_user, sort=hm_sort or 'newest'):
                audio_tracks.append({
                    'type':       'audio',
                    'id':         None,
                    'title':      hm['title'],
                    'artist':     a_name,
                    'genre':      '',
                    'stream_url': hm['stream_url'],
                    'source_url': f'/artists/{a_slug}/',
                    'art_url':    hm.get('thumbnail', ''),
                })

    # ── Video tracks (ALL only) ───────────────────────────────────────────────
    videos   = []
    live_now = []

    if not genre_filter:
        now = timezone.now()
        upcoming_cutoff = now + timedelta(days=30)

        _upcoming = (
            Event.objects.filter(
                artists__isnull=False,
                start_date__gte=now,
                start_date__lte=upcoming_cutoff,
                status='approved',
            )
            .order_by('start_date')
            .values('artists', 'slug')
        )
        upcoming_ids, upcoming_slug = set(), {}
        for row in _upcoming:
            aid = row['artists']
            upcoming_ids.add(aid)
            if aid not in upcoming_slug:
                upcoming_slug[aid] = row['slug']

        all_vt = list(
            VideoTrack.objects.filter(is_active=True)
            .select_related('artist', 'promoter', 'venue')
            .order_by('-published_at')[:500]
        )

        def _video_source_url(v):
            if v.artist_id   and v.artist   and v.artist.slug:   return f'/artists/{v.artist.slug}/'
            if v.promoter_id and v.promoter and v.promoter.slug: return f'/promoters/{v.promoter.slug}/'
            if v.venue_id    and v.venue    and v.venue.slug:    return f'/venues/{v.venue.slug}/'
            return ''

        def _ser_video(v):
            return {
                'type':                  v.source_type,
                'id':                    None,
                'title':                 v.title,
                'artist':                v.artist_name_display or v.channel_title,
                'genre':                 '',
                'video_id':              v.youtube_video_id or v.twitch_video_id or v.twitch_username,
                'embed_url':             v.embed_url,
                'art_url':               v.thumbnail_url,
                'source_url':            _video_source_url(v),
                'is_live':               v.is_live,
                'viewer_count':          v.live_viewer_count,
                'has_show_soon':         bool(v.artist_id and v.artist_id in upcoming_ids),
                'show_soon_event_slug':  upcoming_slug.get(v.artist_id, '') if v.artist_id else '',
            }

        live_objs = [v for v in all_vt if v.is_live]
        vod_objs  = [v for v in all_vt if not v.is_live]

        # Weighted shuffle: artists with upcoming shows get 3× weight
        pool = []
        for v in vod_objs:
            w = 3 if (v.artist_id and v.artist_id in upcoming_ids) else 1
            pool.extend([v] * w)
        random.shuffle(pool)
        seen, shuffled = set(), []
        for v in pool:
            if v.pk not in seen:
                seen.add(v.pk)
                shuffled.append(v)

        live_now = [_ser_video(v) for v in live_objs]
        videos   = [_ser_video(v) for v in shuffled]

    # ── Interleave: 1 video every 8 audio tracks ─────────────────────────────
    if not genre_filter and videos:
        tracks, vi = [], 0
        for i, a in enumerate(audio_tracks):
            tracks.append(a)
            if (i + 1) % 8 == 0 and vi < len(videos):
                tracks.append(videos[vi])
                vi += 1
        tracks.extend(videos[vi:])
    else:
        tracks = audio_tracks

    genres = list(
        Genre.objects.filter(tracks__isnull=False)
        .values_list('name', flat=True)
        .distinct()
        .order_by('name')
    )

    return JsonResponse({'tracks': tracks, 'genres': genres, 'live_now': live_now})


@login_required
def toggle_save_track(request):
    """POST {id: <track_pk>} → toggles SavedTrack, returns {saved: bool}."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    import json as _json
    try:
        body = _json.loads(request.body)
        track_id = int(body.get('id', 0))
    except Exception:
        return JsonResponse({'error': 'bad request'}, status=400)
    track = get_object_or_404(PlaylistTrack, pk=track_id)
    obj, created = SavedTrack.objects.get_or_create(user=request.user, track=track)
    if not created:
        obj.delete()
        return JsonResponse({'saved': False})
    return JsonResponse({'saved': True})


def react_track(request):
    """POST {id: track_pk, reaction: 'up'|'down'} → toggles reaction, returns {reaction, ups, downs}."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'login required'}, status=401)
    import json as _json
    try:
        body = _json.loads(request.body)
        track_id = int(body.get('id', 0))
        reaction = body.get('reaction', '').lower()
    except Exception:
        return JsonResponse({'error': 'bad request'}, status=400)
    if reaction not in ('up', 'down'):
        return JsonResponse({'error': 'invalid reaction'}, status=400)
    track = get_object_or_404(PlaylistTrack, pk=track_id)
    existing = TrackReaction.objects.filter(user=request.user, track=track).first()
    if existing:
        if existing.reaction == reaction:
            existing.delete()
            new_reaction = None
        else:
            existing.reaction = reaction
            existing.save()
            new_reaction = reaction
    else:
        TrackReaction.objects.create(user=request.user, track=track, reaction=reaction)
        new_reaction = reaction
    ups   = TrackReaction.objects.filter(track=track, reaction='up').count()
    downs = TrackReaction.objects.filter(track=track, reaction='down').count()
    return JsonResponse({'reaction': new_reaction, 'ups': ups, 'downs': downs})


def saved_tracks_json(request):
    """Returns saved tracks for the current user (used by SAVED channel in player)."""
    if not request.user.is_authenticated:
        return JsonResponse({'tracks': [], 'genres': []})

    def source_url(t):
        if t.artist:
            return f'/artists/{t.artist.slug}/'
        if t.promoter:
            return f'/promoters/{t.promoter.slug}/'
        if t.venue:
            return f'/venues/{t.venue.slug}/'
        return ''

    saved = SavedTrack.objects.filter(user=request.user).select_related(
        'track__genre', 'track__artist', 'track__promoter', 'track__venue'
    ).order_by('-created_at')

    tracks = [
        {
            'id':         s.track.pk,
            'title':      s.track.title,
            'artist':     s.track.artist_name or s.track.source_label,
            'genre':      s.track.genre.name if s.track.genre else s.track.genre_raw,
            'stream_url': s.track.stream_url,
            'source_url': source_url(s.track),
            'saved':      True,
            'art_url':    s.track.artist.photo.url if (s.track.artist and s.track.artist.photo) else '',
        }
        for s in saved
    ]
    return JsonResponse({'tracks': tracks, 'genres': []})


# ── Video queue (MTV Channel 4) ────────────────────────────────────────────────

def api_video_queue(request):
    """
    Returns a weighted-shuffled list of VideoTrack records for the MTV channel.

    Ordering rules:
      1. Currently live Twitch streams → always first
      2. Artists playing in the next 30 days → 3× weight in shuffle
      3. Everything else → normal weight

    Response includes source_type and embed_url so the player JS
    can branch on YouTube vs Twitch without knowing URL patterns.
    """
    import random
    from datetime import timedelta

    now = timezone.now()
    upcoming_cutoff = now + timedelta(days=30)

    # Artist IDs with shows coming up → map to first event slug for the badge link
    from django.db.models import Min
    _upcoming_events = (
        Event.objects.filter(
            artists__isnull=False,
            start_date__gte=now,
            start_date__lte=upcoming_cutoff,
            status='approved',
        )
        .order_by('start_date')
        .values('artists', 'slug', 'start_date')
    )
    upcoming_artist_ids = set()
    upcoming_artist_slug: dict = {}  # artist_id → first event slug
    for row in _upcoming_events:
        aid = row['artists']
        upcoming_artist_ids.add(aid)
        if aid not in upcoming_artist_slug:
            upcoming_artist_slug[aid] = row['slug']

    all_videos = list(
        VideoTrack.objects.filter(is_active=True)
        .select_related('artist', 'promoter', 'venue')
        .order_by('-is_live', '-published_at')[:500]
    )

    if not all_videos:
        return JsonResponse({'videos': []})

    # Split live streams out — they always lead the queue
    live   = [v for v in all_videos if v.is_live]
    others = [v for v in all_videos if not v.is_live]

    # Weighted shuffle of non-live videos
    pool = []
    for v in others:
        weight = 3 if (v.artist_id and v.artist_id in upcoming_artist_ids) else 1
        pool.extend([v] * weight)

    random.shuffle(pool)
    seen, shuffled = set(), []
    for v in pool:
        if v.pk not in seen:
            seen.add(v.pk)
            shuffled.append(v)

    queue = live + shuffled

    def source_url(v):
        if v.artist_id and v.artist and v.artist.slug:
            return f'/artists/{v.artist.slug}/'
        if v.promoter_id and v.promoter and v.promoter.slug:
            return f'/promoters/{v.promoter.slug}/'
        if v.venue_id and v.venue and v.venue.slug:
            return f'/venues/{v.venue.slug}/'
        return ''

    return JsonResponse({'videos': [
        {
            'video_id':      v.youtube_video_id,
            'source_type':   v.source_type,
            'embed_url':     v.embed_url,
            'title':         v.title,
            'artist':        v.artist_name_display or v.channel_title,
            'thumbnail':     v.thumbnail_url,
            'source_url':    source_url(v),
            'is_live':       v.is_live,
            'viewer_count':  v.live_viewer_count,
            'has_show_soon':         bool(v.artist_id and v.artist_id in upcoming_artist_ids),
            'show_soon_event_slug':  upcoming_artist_slug.get(v.artist_id, '') if v.artist_id else '',
        }
        for v in queue
    ]})


# ── Promoter / Crew views ───────────────────────────────────────────────────────

def promoter_list(request):
    q = request.GET.get('q', '').strip()
    active_type = request.GET.get('type', '').strip()
    qs = PromoterProfile.objects.filter(is_public=True)
    if q:
        qs = qs.filter(name__icontains=q)
        if request.headers.get('Accept', '').startswith('application/json') or request.GET.get('format') == 'json':
            return JsonResponse({'promoters': [{'id': p.pk, 'name': p.name} for p in qs[:10]]})
    if active_type:
        valid = [k for k, _ in PromoterProfile.TYPE_CHOICES]
        if active_type in valid:
            qs = qs.filter(promoter_type__contains=[active_type])
    return render(request, 'events/promoter_list.html', {
        'promoters': qs.order_by('name'),
        'active_type': active_type,
    })


def _discogs_fetch_by_url(discogs_url):
    """
    Given a Discogs release/master/sell URL, extract the ID and fetch
    cover image + label + year via the API.  Returns dict or {}.
    """
    import re as _re2
    # Extract release or master ID from various Discogs URL formats
    rel_m  = _re2.search(r'/release/(\d+)', discogs_url)
    mast_m = _re2.search(r'/master/(\d+)', discogs_url)
    sell_rel = _re2.search(r'/sell/release/(\d+)', discogs_url)
    sell_mast = _re2.search(r'[?&]master_id=(\d+)', discogs_url)

    api_url = None
    disc_id = None
    if rel_m:
        disc_id = rel_m.group(1)
        api_url = f'https://api.discogs.com/releases/{disc_id}'
    elif sell_rel:
        disc_id = sell_rel.group(1)
        api_url = f'https://api.discogs.com/releases/{disc_id}'
    elif mast_m:
        disc_id = mast_m.group(1)
        api_url = f'https://api.discogs.com/masters/{disc_id}'
    elif sell_mast:
        disc_id = sell_mast.group(1)
        api_url = f'https://api.discogs.com/masters/{disc_id}'

    if not api_url:
        return {}

    _headers = {
        'User-Agent': 'CommunityPlaylist/1.0 +https://communityplaylist.com',
        'Accept': 'application/json',
    }

    def _fetch(url):
        r = requests.get(url, timeout=8, headers=_headers)
        r.raise_for_status()
        return r.json()

    try:
        d = _fetch(api_url)
        is_master = 'main_release' in d or 'main_release_url' in d

        images = d.get('images') or []
        cover = next((i['uri'] for i in images if i.get('type') == 'primary'), '')
        if not cover and images:
            cover = images[0].get('uri', '')

        labels = d.get('labels') or []
        label  = labels[0].get('name', '') if labels else ''
        year   = str(d.get('year', '') or '')[:4]
        genres = d.get('genres') or []
        styles = d.get('styles') or []

        # Masters don't carry labels — follow main_release to get them
        if is_master and (not label or not cover):
            main_rel_url = d.get('main_release_url') or (
                f"https://api.discogs.com/releases/{d['main_release']}" if d.get('main_release') else None
            )
            if main_rel_url:
                import time as _t2; _t2.sleep(0.5)
                try:
                    rel = _fetch(main_rel_url)
                    if not label:
                        rel_labels = rel.get('labels') or []
                        label = rel_labels[0].get('name', '') if rel_labels else ''
                    if not cover:
                        rel_imgs = rel.get('images') or []
                        cover = next((i['uri'] for i in rel_imgs if i.get('type') == 'primary'), '')
                        if not cover and rel_imgs:
                            cover = rel_imgs[0].get('uri', '')
                    if not year:
                        year = str(rel.get('year', '') or '')[:4]
                    if not genres:
                        genres = rel.get('genres') or []
                    if not styles:
                        styles = rel.get('styles') or []
                except Exception:
                    pass

        # Grab first embeddable YouTube video URL for preview player
        def _yt_urls(data):
            """Collect all YouTube URLs from a Discogs release dict."""
            out = []
            for v in (data.get('videos') or []):
                uri = v.get('uri', '')
                if 'youtube.com' in uri or 'youtu.be' in uri:
                    out.append(uri)
            return out

        def _first_embeddable(urls):
            """Return the first URL that YouTube's oEmbed says is embeddable, or ''."""
            for url in urls:
                try:
                    oe = requests.get(
                        'https://www.youtube.com/oembed',
                        params={'url': url, 'format': 'json'},
                        headers={'User-Agent': 'CommunityPlaylist/1.0 +https://communityplaylist.com'},
                        timeout=5,
                    )
                    if oe.status_code == 200:
                        return url
                except Exception:
                    continue
            return ''

        yt_urls = _yt_urls(d)
        # Masters: also check the main release for videos
        if is_master and not yt_urls:
            try:
                main_rel_url2 = d.get('main_release_url') or (
                    f"https://api.discogs.com/releases/{d['main_release']}" if d.get('main_release') else None
                )
                if main_rel_url2:
                    rel2 = _fetch(main_rel_url2)
                    yt_urls = _yt_urls(rel2)
            except Exception:
                pass

        preview_url = _first_embeddable(yt_urls)

        return {
            'cover_url':   cover,
            'label':       label,
            'year':        year,
            'discogs_id':  str(disc_id),
            'genres':      genres,
            'styles':      styles,
            'preview_url': preview_url,
        }
    except Exception:
        return {}


def _sync_record_shop(promoter):
    """
    Fetch the promoter's Google Sheet (CSV), upsert RecordListing rows,
    and fill missing metadata from Discogs.  Returns (created, updated) counts.

    Supported URL types:
      - Regular share URL:   /spreadsheets/d/SHEET_ID/edit...
      - Publish to web URL:  /spreadsheets/d/e/PUBLISHED_ID/pubhtml
    Column headers (case-insensitive, any order, leading cols ignored):
      Artist | Title | Label | Year | Format | Condition | Price | Discogs | Notes
    """
    import csv, io, time as _t
    import re as _re
    url = promoter.shop_sheet_url.strip()
    if not url:
        return 0, 0

    # Detect URL type and build CSV endpoint
    pub_m = _re.search(r'/spreadsheets/d/e/([A-Za-z0-9_-]+)/', url)
    if pub_m:
        # Published-to-web URL → just swap pubhtml for pub?output=csv
        pub_id = pub_m.group(1)
        gid = _re.search(r'[?&]gid=(\d+)', url)
        gid_param = f'&gid={gid.group(1)}' if gid else ''
        csv_candidates = [
            f'https://docs.google.com/spreadsheets/d/e/{pub_id}/pub?output=csv{gid_param}',
        ]
    else:
        reg_m = _re.search(r'/spreadsheets/d/([A-Za-z0-9_-]+)', url)
        if not reg_m:
            return 0, 0
        sheet_id = reg_m.group(1)
        gid = _re.search(r'[?&]gid=(\d+)', url)
        gid_param = f'&gid={gid.group(1)}' if gid else ''
        csv_candidates = [
            f'https://docs.google.com/spreadsheets/d/{sheet_id}/pub?output=csv{gid_param}',
            f'https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv{gid_param}',
        ]

    text = None
    for csv_url in csv_candidates:
        try:
            resp = requests.get(csv_url, timeout=15,
                                headers={'User-Agent': 'CommunityPlaylist/1.0'})
            if resp.status_code == 200 and resp.text.strip() and not resp.text.strip().startswith('<!'):
                text = resp.text
                break
        except Exception:
            continue
    if not text:
        return 0, 0

    # Find the header row — first row with at least one recognisable column name
    KNOWN_HEADERS = {'artist', 'title', 'label', 'price', 'discogs', 'format',
                     'condition', 'year', 'notes', 'note', 'sol', 'youtube', 'preview', 'video'}
    lines = [l for l in text.splitlines() if l.strip()]
    header_line_idx = 0
    for i, line in enumerate(lines):
        cells = [c.strip().lower() for c in line.split(',')]
        if any(c in KNOWN_HEADERS for c in cells):
            header_line_idx = i
            break
    cleaned_text = '\n'.join(lines[header_line_idx:])

    reader = csv.DictReader(io.StringIO(cleaned_text))

    def _h(row, *keys):
        """Case-insensitive column lookup, strips whitespace."""
        for k in keys:
            for rk in row:
                if rk.strip().lower() == k.lower():
                    v = row[rk]
                    return v.strip() if v else ''
        return ''

    # Max Discogs API calls per sync request — keeps response under gunicorn timeout.
    # Records beyond the cap get enriched on the next sync call.
    DISCOGS_PER_SYNC = 10

    created = updated = 0
    discogs_calls = 0
    current_row_indices = set()
    data_idx = 0  # counts only rows that have artist+title

    for row in reader:
        artist = _h(row, 'artist')
        title  = _h(row, 'title')
        if not artist or not title:
            continue
        data_idx += 1
        idx = data_idx

        label       = _h(row, 'label')
        year        = _h(row, 'year')
        fmt         = _h(row, 'format')
        condition   = _h(row, 'condition', 'cond')
        price_raw   = _h(row, 'price sol', 'price (sol)', 'price_sol', 'price', 'sol')
        discogs_url = _h(row, 'discogs', 'discogs url', 'discogs_url')
        notes       = _h(row, 'notes', 'note', 'comments')
        sheet_yt    = _h(row, 'youtube', 'youtube url', 'preview', 'preview url', 'video')

        # price_display: keep the raw string; price_sol: extract numeric SOL value if present
        price_display = price_raw
        import re as _re2
        sol_m = _re2.search(r'([\d.,]+)\s*(?:sol)?$', price_raw.lower().replace('sol', '').strip())
        # Only treat as SOL if no $ sign
        if price_raw and '$' not in price_raw:
            try:
                price_sol = float(price_raw.replace(',', '.').strip())
            except ValueError:
                price_sol = 0.0
        else:
            price_sol = 0.0

        listing, is_new = RecordListing.objects.get_or_create(
            promoter=promoter, row_index=idx,
            defaults={'artist': artist, 'title': title},
        )

        listing.artist        = artist
        listing.title         = title
        # Only overwrite label/year from sheet if sheet has a value;
        # otherwise preserve previously Discogs-enriched data
        if label:
            listing.label = label
        if year:
            listing.year = year
        listing.format        = fmt
        listing.condition     = condition[:4] if condition else ''
        listing.price_sol     = price_sol
        listing.price_display = price_display
        listing.notes         = notes
        listing.is_available  = True

        # Sheet YouTube column overrides Discogs video lookup
        if sheet_yt and ('youtube.com' in sheet_yt or 'youtu.be' in sheet_yt):
            listing.preview_url = sheet_yt

        # Discogs enrichment — only for records missing cover, metadata, or preview,
        # and cap at DISCOGS_PER_SYNC calls per request to avoid worker timeout
        needs_cover   = not listing.cover_url
        needs_meta    = not listing.label or not listing.year
        needs_preview = not listing.preview_url  # False if sheet already set it

        if (needs_cover or needs_meta or needs_preview) and discogs_calls < DISCOGS_PER_SYNC:
            disc = {}
            if discogs_url:
                disc = _discogs_fetch_by_url(discogs_url)
                discogs_calls += 1
                _t.sleep(0.4)
            if not disc:
                disc = _discogs_search(artist, title)
                discogs_calls += 1
                _t.sleep(0.4)
            if disc:
                if needs_cover and disc.get('cover_url'):
                    listing.cover_url = disc['cover_url']
                if not listing.label and disc.get('label'):
                    listing.label = disc['label']
                if not listing.year and disc.get('year'):
                    listing.year = disc['year']
                if not listing.discogs_id and disc.get('discogs_id'):
                    listing.discogs_id = disc['discogs_id']
                if disc.get('genres') and not listing.genres:
                    listing.genres = ', '.join(disc['genres'][:5])
                if disc.get('styles') and not listing.styles:
                    listing.styles = ', '.join(disc['styles'][:8])
                if disc.get('preview_url') and not listing.preview_url:
                    listing.preview_url = disc['preview_url']

        listing.save()
        current_row_indices.add(idx)
        if is_new:
            created += 1
        else:
            updated += 1

    # Mark rows no longer in sheet as unavailable
    RecordListing.objects.filter(promoter=promoter).exclude(
        row_index__in=current_row_indices
    ).update(is_available=False)

    return created, updated


def promoter_detail(request, slug):
    promoter = get_object_or_404(PromoterProfile, slug=slug, is_public=True)

    session_key = f'viewed_promoter_{promoter.pk}'
    if not request.session.get(session_key):
        PromoterProfile.objects.filter(pk=promoter.pk).update(view_count=models.F('view_count') + 1)
        request.session[session_key] = True
        promoter.view_count += 1

    tracks = promoter.tracks.select_related('genre').order_by('position', 'title')
    can_edit = request.user.is_authenticated and (
        request.user.is_staff or promoter.claimed_by == request.user
    )
    is_following = (
        request.user.is_authenticated and
        Follow.objects.filter(
            user=request.user, target_type='promoter', target_id=promoter.pk
        ).exists()
    )
    saved_ids = set(
        SavedTrack.objects.filter(user=request.user, track__in=tracks).values_list('track_id', flat=True)
    ) if request.user.is_authenticated else set()
    yt_embed_html = _get_yt_embed_cached(promoter.youtube) if _is_yt_channel(promoter.youtube) else ''
    _twitch_data  = _get_twitch_clips_cached(promoter.twitch) if promoter.twitch else {}
    twitch_clips  = _twitch_data.get('clips', [])
    twitch_vods   = _twitch_data.get('vods', [])
    shared_tracks = []  # TrackShare feature removed

    listings = list(promoter.record_listings.filter(is_available=True)) if promoter.shop_sheet_url else []

    # Annotate each listing with its pending reservation count for seller view
    if can_edit and listings:
        from django.db.models import Count
        res_counts = dict(
            RecordReservation.objects.filter(
                listing__promoter=promoter,
                status=RecordReservation.STATUS_PENDING,
            ).values('listing_id').annotate(n=Count('id')).values_list('listing_id', 'n')
        )
        for l in listings:
            l.reservation_count = res_counts.get(l.pk, 0)
    else:
        for l in listings:
            l.reservation_count = 0

    pending_reservations = (RecordReservation.objects
                            .filter(listing__promoter=promoter,
                                    status=RecordReservation.STATUS_PENDING)
                            .count()) if can_edit else 0

    from django.utils import timezone
    upcoming_events = (Event.objects
                       .filter(promoters=promoter, start_date__gte=timezone.now(), status='approved')
                       .order_by('start_date')[:12])

    return render(request, 'events/promoter_detail.html', {
        'promoter': promoter, 'tracks': tracks,
        'can_edit': can_edit, 'is_following': is_following,
        'saved_ids': saved_ids,
        'yt_embed_html': yt_embed_html,
        'twitch_clips': twitch_clips,
        'twitch_vods': twitch_vods,
        'shared_tracks': shared_tracks,
        'members': promoter.members.order_by('name'),
        'listings': listings,
        'pending_reservations': pending_reservations,
        'upcoming_events': upcoming_events,
    })


def promoter_reserve(request, slug, listing_pk):
    """POST — create a reservation for a record listing. Returns JSON."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    promoter = get_object_or_404(PromoterProfile, slug=slug, is_public=True)
    listing  = get_object_or_404(RecordListing, pk=listing_pk, promoter=promoter, is_available=True)

    name    = request.POST.get('name', '').strip()
    email   = request.POST.get('email', '').strip()
    contact = request.POST.get('contact', '').strip()
    message = request.POST.get('message', '').strip()

    if not name:
        return JsonResponse({'error': 'Name is required.'}, status=400)

    # Pre-fill from logged-in user if not provided
    if request.user.is_authenticated:
        name    = name or request.user.get_full_name() or request.user.username
        email   = email or request.user.email

    reservation = RecordReservation.objects.create(
        listing=listing,
        buyer=request.user if request.user.is_authenticated else None,
        buyer_name=name,
        buyer_email=email,
        buyer_contact=contact,
        message=message,
        status=RecordReservation.STATUS_PENDING,
    )
    return JsonResponse({
        'ok': True,
        'reservation_id': reservation.pk,
        'message': f'Reserved! {promoter.name} will be in touch.',
    })


@login_required
def promoter_reservations(request, slug):
    """Seller view — list all reservations for this promoter's shop."""
    promoter = get_object_or_404(PromoterProfile, slug=slug)
    if not (request.user.is_staff or promoter.claimed_by == request.user):
        return redirect('promoter_detail', slug=slug)

    reservations = (RecordReservation.objects
                    .filter(listing__promoter=promoter)
                    .select_related('listing')
                    .order_by('status', '-created_at'))

    if request.method == 'POST':
        # Quick status update from seller
        res_pk = request.POST.get('reservation_pk')
        new_status = request.POST.get('status')
        if res_pk and new_status in dict(RecordReservation.STATUS_CHOICES):
            RecordReservation.objects.filter(
                pk=res_pk, listing__promoter=promoter
            ).update(status=new_status)
        return redirect('promoter_reservations', slug=slug)

    return render(request, 'events/promoter_reservations.html', {
        'promoter': promoter,
        'reservations': reservations,
        'statuses': RecordReservation.STATUS_CHOICES,
    })


@login_required
def promoter_sync_shop(request, slug):
    """Trigger a manual sync of the promoter's record shop Google Sheet."""
    promoter = get_object_or_404(PromoterProfile, slug=slug)
    if not (request.user.is_staff or promoter.claimed_by == request.user):
        return redirect('promoter_detail', slug=slug)
    if not promoter.shop_sheet_url:
        messages.warning(request, 'No sheet URL set — add one in your profile settings.')
        return redirect('promoter_edit', slug=slug)
    created, updated = _sync_record_shop(promoter)
    messages.success(request, f'Shop synced: {created} new, {updated} updated.')
    return redirect('promoter_detail', slug=slug)


@login_required
def promoter_register(request):
    """Create or claim a promoter/crew profile."""
    if request.method == 'GET':
        return render(request, 'events/promoter_register.html', {})

    name             = request.POST.get('name', '').strip()
    bio              = request.POST.get('bio', '').strip()
    website          = request.POST.get('website', '').strip()
    drive_folder_url = request.POST.get('drive_folder_url', '').strip()
    photo            = request.FILES.get('photo')

    # Validate types (multiple allowed)
    valid_types = [k for k, _ in PromoterProfile.TYPE_CHOICES]
    promoter_type = [t for t in request.POST.getlist('promoter_type') if t in valid_types]
    if not promoter_type:
        promoter_type = [PromoterProfile.TYPE_CREW]

    errors = {}
    if not name:
        errors['name'] = 'Name is required.'
    elif PromoterProfile.objects.filter(name__iexact=name).exists():
        errors['name'] = f'"{name}" already exists — search for it and request to be added as an admin.'

    if errors:
        return render(request, 'events/promoter_register.html', {'errors': errors, 'prev': request.POST})

    p = PromoterProfile.objects.create(
        name=name, bio=bio, website=website,
        drive_folder_url=drive_folder_url,
        promoter_type=promoter_type,
        claimed_by=request.user,
    )
    if photo:
        p.photo = photo
        p.save(update_fields=['photo'])

    return redirect('promoter_detail', slug=p.slug)


@login_required
def promoter_edit(request, slug):
    promoter = get_object_or_404(PromoterProfile, slug=slug)
    if not (request.user.is_staff or promoter.claimed_by == request.user):
        return redirect('promoter_detail', slug=slug)

    SOCIAL_FIELDS = ['instagram', 'soundcloud', 'bandcamp', 'mixcloud', 'youtube',
                     'spotify', 'mastodon', 'bluesky', 'kofi', 'tiktok', 'discord', 'telegram', 'twitch']

    all_artists = Artist.objects.order_by('name')
    type_choices = PromoterProfile.TYPE_CHOICES
    all_genres = Genre.objects.order_by('name')
    if request.method == 'GET':
        return render(request, 'events/promoter_edit.html', {
            'promoter': promoter,
            'all_artists': all_artists,
            'member_pks': set(promoter.members.values_list('pk', flat=True)),
            'type_choices': type_choices,
            'all_genres': all_genres,
            'selected_genre_pks': set(promoter.genres.values_list('pk', flat=True)),
        })

    promoter.shop_pay_in_person = 'shop_pay_in_person' in request.POST
    promoter.shop_open_to_trade = 'shop_open_to_trade' in request.POST
    promoter.accept_demos       = 'accept_demos' in request.POST
    old_drive_p = promoter.drive_folder_url or ''
    import re as _re2
    bc_p = request.POST.get('brand_color', '').strip()
    if _re2.fullmatch(r'#[0-9a-fA-F]{6}', bc_p):
        promoter.brand_color = bc_p.lower()
    elif not bc_p:
        promoter.brand_color = ''
    for field in ['name', 'bio', 'website', 'drive_folder_url',
                  'shop_sheet_url', 'sol_wallet'] + SOCIAL_FIELDS:
        val = request.POST.get(field, '').strip()
        setattr(promoter, field, val)
    # Promoter type
    valid_types = [k for k, _ in PromoterProfile.TYPE_CHOICES]
    pt = [t for t in request.POST.getlist('promoter_type') if t in valid_types]
    if pt:
        promoter.promoter_type = pt
    if request.FILES.get('photo'):
        promoter.photo = request.FILES['photo']
    promoter.save()

    # Update member artists
    selected_pks = [int(x) for x in request.POST.getlist('members') if x.isdigit()]
    promoter.members.set(Artist.objects.filter(pk__in=selected_pks))

    # Update genres
    genre_pks = [int(x) for x in request.POST.getlist('genres') if x.isdigit()]
    promoter.genres.set(Genre.objects.filter(pk__in=genre_pks))

    if old_drive_p and not promoter.drive_folder_url:
        PlaylistTrack.objects.filter(promoter=promoter).delete()
    messages.success(request, 'Profile updated.')
    return redirect('promoter_detail', slug=promoter.slug)


@login_required
def submit_demo(request, slug):
    return JsonResponse({'error': 'Demo submissions have been removed.'}, status=410)


@login_required
def delete_track_share(request, pk):
    return JsonResponse({'error': 'Demo submissions have been removed.'}, status=410)


def api_event_detail(request, slug):
    """Lightweight JSON for the fixed event panel — no page navigation needed."""
    event = get_object_or_404(Event, slug=slug, status='approved')

    photo_url = ''
    approved = event.photos.filter(approved=True).first()
    if approved:
        photo_url = approved.image.url
    elif event.photo:
        photo_url = event.photo.url

    data = {
        'title':            event.title,
        'slug':             event.slug,
        'description':      event.description,
        'start_date':       localtime(event.start_date).strftime('%a, %b %-d @ %-I:%M %p'),
        'end_date':         localtime(event.end_date).strftime('%a, %b %-d @ %-I:%M %p') if event.end_date else '',
        'location':         event.location,
        'neighborhood':     event.neighborhood,
        'category':         event.category,
        'category_display': event.get_category_display() if event.category else '',
        'is_free':          event.is_free,
        'price_info':       event.price_info,
        'website':          event.website,
        'extra_links':      event.extra_links or [],
        'photo_url':        photo_url,
        'genres':           [g.name for g in event.genres.all()],
        'lat':              event.latitude,
        'lng':              event.longitude,
    }
    return JsonResponse(data)


def api_shelters(request):
    """All active shelters as JSON for the resource hub map."""
    import math

    shelters = Shelter.objects.filter(active=True)

    # Optional weather-filter: ?alert=hot|cold|smoke returns only relevant shelters
    alert = request.GET.get('alert', '')
    if alert == 'hot':
        shelters = shelters.filter(available_hot=True)
    elif alert == 'cold':
        shelters = shelters.filter(available_cold=True)
    elif alert == 'smoke':
        shelters = shelters.filter(available_smoke=True)

    data = [s.as_map_dict() for s in shelters]

    # Optional proximity sort: ?lat=&lng= sorts by distance
    try:
        user_lat = float(request.GET.get('lat', ''))
        user_lng = float(request.GET.get('lng', ''))
        def _dist(s):
            if s['latitude'] is None or s['longitude'] is None:
                return 9999
            dlat = math.radians(s['latitude'] - user_lat)
            dlng = math.radians(s['longitude'] - user_lng)
            a = math.sin(dlat/2)**2 + math.cos(math.radians(user_lat)) * math.cos(math.radians(s['latitude'])) * math.sin(dlng/2)**2
            return 3958.8 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        for s in data:
            s['distance_miles'] = round(_dist(s), 2)
        data.sort(key=lambda s: s['distance_miles'])
    except (TypeError, ValueError):
        pass

    return JsonResponse({'shelters': data})


# ── Global Shop ────────────────────────────────────────────────────────────────

def shop(request):
    """Aggregate record shop — all available listings across all promoters."""
    from django.db.models import Q
    listings = (
        RecordListing.objects
        .filter(is_available=True)
        .select_related('promoter')
        .order_by('artist', 'title')
    )

    # Optional filters
    q       = request.GET.get('q', '').strip()
    style   = request.GET.get('style', '').strip()
    fmt     = request.GET.get('format', '').strip()
    sort    = request.GET.get('sort', 'artist')

    if q:
        listings = listings.filter(Q(artist__icontains=q) | Q(title__icontains=q) | Q(label__icontains=q))
    if style:
        listings = listings.filter(styles__icontains=style)
    if fmt:
        listings = listings.filter(format__icontains=fmt)

    sort_map = {
        'artist': 'artist',
        'price_lo': 'price_sol',
        'price_hi': '-price_sol',
        'newest': '-synced_at',
    }
    listings = listings.order_by(sort_map.get(sort, 'artist'))

    # Distinct styles for filter chips
    all_styles = sorted({
        s.strip()
        for r in RecordListing.objects.filter(is_available=True).values_list('styles', flat=True)
        for s in (r or '').split(',') if s.strip()
    })

    return render(request, 'events/shop.html', {
        'listings':   listings,
        'all_styles': all_styles,
        'q':          q,
        'style':      style,
        'sort':       sort,
        'total':      listings.count(),
    })


# ── RSS Feed for new approved events (Zapier / IFTTT trigger) ─────────────────

def events_rss(request):
    """RSS 2.0 feed of recently approved events — consumed by Zapier for social posting."""
    from django.utils.feedgenerator import Rss201rev2Feed, Enclosure
    import io

    now      = timezone.now()
    category = request.GET.get('category', '')
    limit    = min(int(request.GET.get('limit', 20)), 50)

    qs = (
        Event.objects.filter(status='approved', start_date__gte=now)
        .order_by('start_date')
    )
    if category:
        qs = qs.filter(category=category)
    qs = qs[:limit]

    feed = Rss201rev2Feed(
        title='Community Playlist PDX — Upcoming Events',
        link='https://communityplaylist.com/',
        description='Portland community events submitted by the people, for the people. No ads, no tracking.',
        language='en',
        author_name='Community Playlist',
        feed_url='https://communityplaylist.com/feed/events.rss',
    )

    for ev in qs:
        start_local = ev.start_date.astimezone(timezone.get_current_timezone())
        date_str    = start_local.strftime('%a %b %-d @ %-I:%M %p')
        location    = ev.location or 'Portland, OR'
        genres      = ', '.join(g.name for g in ev.genres.all()[:4])
        desc_parts  = [f'📅 {date_str}', f'📍 {location}']
        if genres:
            desc_parts.append(f'🎵 {genres}')
        if ev.description:
            desc_parts.append(ev.description[:300])
        desc_parts.append(f'🔗 https://communityplaylist.com/events/{ev.slug}/')

        photo_url = None
        if ev.photo:
            photo_url = f'https://communityplaylist.com{ev.photo.url}'

        feed.add_item(
            title=ev.title,
            link=f'https://communityplaylist.com/events/{ev.slug}/',
            description='\n\n'.join(desc_parts),
            pubdate=ev.created_at,
            unique_id=f'{ev.slug}@communityplaylist.com',
            enclosures=[Enclosure(photo_url, "0", "image/jpeg")] if photo_url else [],
        )

    buf = io.StringIO()
    feed.write(buf, 'utf-8')
    return HttpResponse(buf.getvalue(), content_type='application/rss+xml; charset=utf-8')


def event_flyer(request, slug):
    """Render a printable / screenshot-able event flyer (portrait + square formats)."""
    if request.user.is_staff:
        event = get_object_or_404(Event, slug=slug)
    else:
        event = get_object_or_404(Event, slug=slug, status='approved')

    start_local = localtime(event.start_date)
    date_str    = start_local.strftime('%A, %B %-d, %Y')
    time_str    = start_local.strftime('%-I:%M %p')

    artists   = list(event.artists.all()[:8])
    genres    = list(event.genres.values_list('name', flat=True)[:6])
    promoters = list(event.promoters.all()[:2])

    if event.is_free:
        price_str = 'FREE'
    elif event.price_info:
        price_str = event.price_info[:40]
    else:
        price_str = ''

    try:
        photo_url = request.build_absolute_uri(event.photo.url) if event.photo else None
    except ValueError:
        photo_url = None

    user_backgrounds = []
    if request.user.is_authenticated:
        for bg in FlyerBackground.objects.filter(owner=request.user):
            url = bg.bg_url
            if url:
                user_backgrounds.append({
                    'pk':    bg.pk,
                    'url':   request.build_absolute_uri(url) if url.startswith('/') else url,
                    'label': bg.label or 'Custom',
                })

    return render(request, 'events/event_flyer.html', {
        'event':            event,
        'date_str':         date_str,
        'time_str':         time_str,
        'artists':          artists,
        'genres':           genres,
        'promoters':        promoters,
        'price_str':        price_str,
        'photo_url':        photo_url,
        'event_url':        f'communityplaylist.com/events/{event.slug}/',
        'user_backgrounds': user_backgrounds,
        'bg_count':         len(user_backgrounds),
    })


@login_required
def flyer_bg_upload(request):
    """Upload a new flyer background (max 10 per user). Returns JSON."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    if FlyerBackground.objects.filter(owner=request.user).count() >= 10:
        return JsonResponse({'error': 'Max 10 backgrounds — delete one first.'}, status=400)
    image = request.FILES.get('image')
    if not image:
        return JsonResponse({'error': 'No image provided.'}, status=400)
    if not image.content_type.startswith('image/'):
        return JsonResponse({'error': 'File must be an image.'}, status=400)
    if image.size > 8 * 1024 * 1024:
        return JsonResponse({'error': 'Image must be under 8 MB.'}, status=400)
    label = request.POST.get('label', '')[:60]
    bg = FlyerBackground.objects.create(owner=request.user, image=image, label=label)
    return JsonResponse({
        'ok':    True,
        'pk':    bg.pk,
        'url':   request.build_absolute_uri(bg.image.url),
        'label': bg.label,
    })


@login_required
def flyer_bg_delete(request, pk):
    """Delete a saved flyer background."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    bg = get_object_or_404(FlyerBackground, pk=pk, owner=request.user)
    if bg.image:
        bg.image.delete(save=False)
    bg.delete()
    return JsonResponse({'ok': True})


def flyer_bg_drive(request):
    """List image files from a Google Drive folder or single file URL."""
    from django.conf import settings
    import urllib.request as _ur, urllib.parse as _up, json as _json
    folder_url = request.GET.get('url', '').strip()
    if not folder_url:
        return JsonResponse({'error': 'No URL provided.'}, status=400)

    # Single file?
    m = re.search(r'/d/([a-zA-Z0-9_-]+)', folder_url)
    if m and '/folders/' not in folder_url:
        fid = m.group(1)
        return JsonResponse({'ok': True, 'images': [
            {'id': fid, 'name': 'Drive image',
             'url': f'https://drive.google.com/thumbnail?id={fid}&sz=w1200'}
        ]})

    # Folder?
    mf = re.search(r'/folders/([a-zA-Z0-9_-]+)', folder_url)
    if not mf:
        return JsonResponse({'error': 'Paste a Google Drive folder or file link.'}, status=400)

    api_key = getattr(settings, 'GOOGLE_DRIVE_API_KEY', '')
    if not api_key:
        return JsonResponse({'error': 'Drive API key not configured on this server.'}, status=400)

    folder_id = mf.group(1)
    q = _up.quote(f"'{folder_id}' in parents and mimeType contains 'image/' and trashed=false")
    api_url = (f'https://www.googleapis.com/drive/v3/files'
               f'?q={q}&fields=files(id,name)&key={api_key}&pageSize=20')
    try:
        with _ur.urlopen(api_url, timeout=10) as resp:
            data = _json.loads(resp.read())
        images = [
            {'id': f['id'], 'name': f['name'],
             'url': f"https://drive.google.com/thumbnail?id={f['id']}&sz=w1200"}
            for f in data.get('files', [])
        ]
        return JsonResponse({'ok': True, 'images': images})
    except Exception as e:
        return JsonResponse({'error': f'Drive API error: {e}'}, status=500)


# ── Last.fm user proxy ────────────────────────────────────────────────────────

def api_lastfm_proxy(request):
    """Server-side proxy for Last.fm API (avoids CORS).
    GET /api/lastfm/?username=X&method=user.gettoptracks&period=1month
    """
    from django.conf import settings as _s
    api_key = getattr(_s, 'LASTFM_API_KEY', '')
    if not api_key:
        return JsonResponse({'error': 'Last.fm API key not configured'}, status=400)

    username = request.GET.get('username', '').strip()
    if not username:
        return JsonResponse({'error': 'username required'}, status=400)

    method = request.GET.get('method', 'user.gettoptracks')
    period = request.GET.get('period', '1month')
    limit  = min(int(request.GET.get('limit', '10')), 20)

    allowed_methods = {'user.gettoptracks', 'user.getrecenttracks', 'user.gettopartists'}
    if method not in allowed_methods:
        return JsonResponse({'error': 'method not allowed'}, status=400)

    try:
        resp = requests.get(
            'https://ws.audioscrobbler.com/2.0/',
            params={
                'method':  method,
                'user':    username,
                'api_key': api_key,
                'format':  'json',
                'period':  period,
                'limit':   limit,
            },
            headers={'User-Agent': 'CommunityPlaylist/1.0 +https://communityplaylist.com'},
            timeout=8,
        )
        resp.raise_for_status()
        return JsonResponse(resp.json())
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ── Discogs collection proxy ──────────────────────────────────────────────────

def api_discogs_proxy(request):
    """Server-side proxy for Discogs user collection (avoids CORS).
    GET /api/discogs/?username=X&page=1
    """
    from django.conf import settings as _s
    consumer_key    = getattr(_s, 'DISCOGS_CONSUMER_KEY', '')
    consumer_secret = getattr(_s, 'DISCOGS_CONSUMER_SECRET', '')
    if not consumer_key:
        return JsonResponse({'error': 'Discogs credentials not configured'}, status=400)

    username = request.GET.get('username', '').strip()
    if not username:
        return JsonResponse({'error': 'username required'}, status=400)

    page     = max(1, min(int(request.GET.get('page', '1')), 10))
    per_page = 8

    try:
        resp = requests.get(
            f'https://api.discogs.com/users/{username}/collection/folders/0/releases',
            params={
                'page':     page,
                'per_page': per_page,
                'sort':     'added',
                'sort_order': 'desc',
            },
            headers={
                'User-Agent':     'CommunityPlaylist/1.0 +https://communityplaylist.com',
                'Authorization':  f'Discogs key={consumer_key}, secret={consumer_secret}',
            },
            timeout=10,
        )
        if resp.status_code == 403:
            return JsonResponse({'releases': [], 'private': True}, status=200)
        resp.raise_for_status()
        data = resp.json()
        # Slim down the response — client only needs cover, title, artist, year, url
        releases = []
        for item in data.get('releases', []):
            bi = item.get('basic_information', {})
            releases.append({
                'id':      bi.get('id'),
                'title':   bi.get('title', ''),
                'artist':  ', '.join(a.get('name', '') for a in bi.get('artists', [])),
                'year':    bi.get('year'),
                'thumb':   bi.get('thumb', ''),
                'cover':   bi.get('cover_image', ''),
                'url':     f"https://www.discogs.com/release/{bi.get('id')}",
            })
        return JsonResponse({
            'releases': releases,
            'pages':    data.get('pagination', {}).get('pages', 1),
            'page':     page,
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ── YouTube channel proxy ─────────────────────────────────────────────────────

_yt_channel_cache: dict = {}
_YT_CHANNEL_TTL = 3600  # 1h

def api_youtube_channel_proxy(request):
    """Resolve a YouTube channel handle → uploads playlist + public playlists.
    GET /api/youtube-channel/?handle=binsky   (no @ prefix needed)
    """
    from django.conf import settings as _s
    import time as _t
    api_key = getattr(_s, 'YOUTUBE_API_KEY', '')
    if not api_key:
        return JsonResponse({'error': 'YouTube API key not configured'}, status=400)

    handle = request.GET.get('handle', '').strip().lstrip('@')
    if not handle:
        return JsonResponse({'error': 'handle required'}, status=400)

    now = _t.time()
    cached = _yt_channel_cache.get(handle)
    if cached and now - cached['ts'] < _YT_CHANNEL_TTL:
        return JsonResponse(cached['data'])

    try:
        r1 = requests.get(
            'https://www.googleapis.com/youtube/v3/channels',
            params={'part': 'snippet,contentDetails', 'forHandle': handle, 'key': api_key},
            headers={'User-Agent': 'CommunityPlaylist/1.0'},
            timeout=8,
        )
        r1.raise_for_status()
        items = r1.json().get('items', [])
        if not items:
            return JsonResponse({'error': 'channel not found'}, status=404)

        ch = items[0]
        channel_id    = ch['id']
        channel_title = ch['snippet']['title']
        uploads_id    = ch['contentDetails']['relatedPlaylists']['uploads']

        r2 = requests.get(
            'https://www.googleapis.com/youtube/v3/playlists',
            params={'part': 'snippet', 'channelId': channel_id, 'maxResults': 12, 'key': api_key},
            headers={'User-Agent': 'CommunityPlaylist/1.0'},
            timeout=8,
        )
        r2.raise_for_status()
        playlists = [
            {
                'id':    p['id'],
                'title': p['snippet']['title'],
                'thumb': (p['snippet'].get('thumbnails', {}).get('medium') or
                          p['snippet'].get('thumbnails', {}).get('default') or {}).get('url', ''),
            }
            for p in r2.json().get('items', [])
        ]

        # Fetch recent videos from uploads playlist (individual video IDs are embeddable)
        r3 = requests.get(
            'https://www.googleapis.com/youtube/v3/playlistItems',
            params={'part': 'snippet', 'playlistId': uploads_id, 'maxResults': 9, 'key': api_key},
            headers={'User-Agent': 'CommunityPlaylist/1.0'},
            timeout=8,
        )
        r3.raise_for_status()
        videos = []
        for item in r3.json().get('items', []):
            sn = item.get('snippet', {})
            vid = sn.get('resourceId', {}).get('videoId', '')
            if not vid:
                continue
            thumbs = sn.get('thumbnails', {})
            thumb = (thumbs.get('medium') or thumbs.get('high') or thumbs.get('default') or {}).get('url', '')
            videos.append({'id': vid, 'title': sn.get('title', ''), 'thumb': thumb})

        data = {
            'channel_id':          channel_id,
            'title':               channel_title,
            'uploads_playlist_id': uploads_id,
            'playlists':           playlists,
            'videos':              videos,
        }
        _yt_channel_cache[handle] = {'data': data, 'ts': now}
        return JsonResponse(data)

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ── YouTube video search proxy ────────────────────────────────────────────────

_yt_search_cache: dict = {}
_YT_SEARCH_TTL = 86400  # 24h

def api_youtube_search_proxy(request):
    """Search YouTube for a query, return first video result.
    GET /api/youtube-search/?q=Fanu+Neverending
    Returns {video_id, title, thumb, channel}
    """
    from django.conf import settings as _s
    import time as _t
    api_key = getattr(_s, 'YOUTUBE_API_KEY', '')
    if not api_key:
        return JsonResponse({'error': 'YouTube API key not configured'}, status=400)

    q = request.GET.get('q', '').strip()
    if not q:
        return JsonResponse({'error': 'q required'}, status=400)

    now = _t.time()
    cached = _yt_search_cache.get(q)
    if cached and now - cached['ts'] < _YT_SEARCH_TTL:
        return JsonResponse(cached['data'])

    try:
        resp = requests.get(
            'https://www.googleapis.com/youtube/v3/search',
            params={
                'part':       'snippet',
                'type':       'video',
                'q':          q,
                'maxResults': 1,
                'key':        api_key,
            },
            headers={'User-Agent': 'CommunityPlaylist/1.0'},
            timeout=8,
        )
        resp.raise_for_status()
        items = resp.json().get('items', [])
        if not items:
            return JsonResponse({'error': 'no results'}, status=404)
        it = items[0]
        sn = it.get('snippet', {})
        data = {
            'video_id': it['id']['videoId'],
            'title':    sn.get('title', ''),
            'channel':  sn.get('channelTitle', ''),
            'thumb':    (sn.get('thumbnails', {}).get('medium') or sn.get('thumbnails', {}).get('default') or {}).get('url', ''),
        }
        _yt_search_cache[q] = {'data': data, 'ts': now}
        return JsonResponse(data)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ── YouTube playlist items proxy ──────────────────────────────────────────────

_yt_playlist_cache: dict = {}
_YT_PLAYLIST_TTL = 3600  # 1h

def api_youtube_playlist_proxy(request):
    """Expand a YouTube playlist into video items.
    GET /api/youtube-playlist/?id=PLxxx
    Returns {items: [{video_id, title, thumb}]}
    """
    from django.conf import settings as _s
    import time as _t
    api_key = getattr(_s, 'YOUTUBE_API_KEY', '')
    if not api_key:
        return JsonResponse({'error': 'YouTube API key not configured'}, status=400)

    playlist_id = request.GET.get('id', '').strip()
    if not playlist_id:
        return JsonResponse({'error': 'id required'}, status=400)

    now = _t.time()
    cached = _yt_playlist_cache.get(playlist_id)
    if cached and now - cached['ts'] < _YT_PLAYLIST_TTL:
        return JsonResponse(cached['data'])

    try:
        resp = requests.get(
            'https://www.googleapis.com/youtube/v3/playlistItems',
            params={'part': 'snippet', 'playlistId': playlist_id, 'maxResults': 50, 'key': api_key},
            headers={'User-Agent': 'CommunityPlaylist/1.0'},
            timeout=10,
        )
        resp.raise_for_status()
        items = []
        for item in resp.json().get('items', []):
            sn = item.get('snippet', {})
            vid = sn.get('resourceId', {}).get('videoId', '')
            if not vid:
                continue
            thumbs = sn.get('thumbnails', {})
            thumb = (thumbs.get('medium') or thumbs.get('high') or thumbs.get('default') or {}).get('url', '')
            items.append({'video_id': vid, 'title': sn.get('title', ''), 'thumb': thumb})
        data = {'items': items}
        _yt_playlist_cache[playlist_id] = {'data': data, 'ts': now}
        return JsonResponse(data)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ── Video Room (Theater) ───────────────────────────────────────────────────────

def video_room(request):
    """Fullscreen theater: PDXTV video queue + live chat."""
    return render(request, 'events/video_room.html')


def player_page(request):
    """Standalone full-page music/video player."""
    return render(request, 'events/player.html')


def player_manifest(request):
    """Web app manifest for standalone player PWA."""
    from django.http import JsonResponse
    return JsonResponse({
        "name": "CP Player · PDX",
        "short_name": "CP Player",
        "start_url": "/player/",
        "scope": "/player/",
        "display": "standalone",
        "background_color": "#0a0a0a",
        "theme_color": "#ff6b35",
        "description": "Community Playlist PDX — music & video player",
        "icons": [],
    })


def video_room_messages(request):
    """GET last 60 chat messages; POST to create one."""
    if request.method == 'POST':
        import json as _json
        try:
            body = _json.loads(request.body)
        except Exception:
            return JsonResponse({'error': 'bad json'}, status=400)
        content = (body.get('content') or '').strip()[:400]
        if not content:
            return JsonResponse({'error': 'empty'}, status=400)
        name = (body.get('name') or '').strip()[:40]
        msg = VideoRoomMessage.objects.create(
            user         = request.user if request.user.is_authenticated else None,
            display_name = '' if request.user.is_authenticated else (name or 'anon'),
            content      = content,
        )
        return JsonResponse({
            'id':         msg.pk,
            'author':     msg.author,
            'content':    msg.content,
            'created_at': msg.created_at.strftime('%H:%M'),
        })
    # GET – return last 60 messages
    msgs = VideoRoomMessage.objects.order_by('-created_at')[:60]
    return JsonResponse({'messages': [
        {'id': m.pk, 'author': m.author, 'content': m.content,
         'created_at': m.created_at.strftime('%H:%M')}
        for m in reversed(list(msgs))
    ]})


def privacy_page(request):
    return render(request, 'events/privacy.html')


def report_page(request):
    submitted = False
    if request.method == 'POST':
        import json
        url    = request.POST.get('url', '').strip()[:500]
        reason = request.POST.get('reason', '').strip()[:2000]
        if url or reason:
            from events.utils.discord import discord_send
            from django.conf import settings
            wh = getattr(settings, 'DISCORD_WEBHOOK_OPS', '')
            if wh:
                discord_send(wh, {'content': f'**Report**\nURL: {url}\nReason: {reason}'})
        submitted = True
    return render(request, 'events/report.html', {'submitted': submitted})


# ── Community Space ────────────────────────────────────────────────────────────

_AUDIO_MIMETYPES = {
    'audio/mpeg', 'audio/mp3', 'audio/wav', 'audio/ogg',
    'audio/flac', 'audio/mp4', 'audio/x-m4a', 'audio/aac',
}
_DOC_MIMETYPES = {
    'application/pdf',
    'application/vnd.google-apps.document',
    'application/vnd.google-apps.presentation',
}


def _fetch_space_library(folder_url, show_audio, show_docs):
    """
    Fetch whitelisted files from a public Google Drive folder.
    Returns (audio_files, doc_files) — each a list of dicts with
    id, name, mimeType, thumbnail_url, preview_url.
    """
    if not folder_url or not (show_audio or show_docs):
        return [], []

    import re as _re_lib
    m = _re_lib.search(r'/folders/([a-zA-Z0-9_-]+)', folder_url)
    if not m:
        return [], []
    folder_id = m.group(1)

    from django.conf import settings as _s_lib
    api_key = getattr(_s_lib, 'GOOGLE_DRIVE_API_KEY', '')
    if not api_key:
        return [], []

    _HDR = {'User-Agent': 'CommunityPlaylist/1.0'}

    def _list_folder(fid, depth=0, max_depth=2):
        """Recursively list all safe files under fid."""
        results = []

        # Build a mimeType OR query for whitelisted types
        wanted = set()
        if show_audio:
            wanted |= _AUDIO_MIMETYPES
        if show_docs:
            wanted |= _DOC_MIMETYPES

        # Fetch files in this folder
        from urllib.parse import quote as _q
        mime_filter = ' or '.join(f"mimeType='{m}'" for m in sorted(wanted))
        q = _q(f"'{fid}' in parents and ({mime_filter}) and trashed=false")
        url = (
            f'https://www.googleapis.com/drive/v3/files'
            f'?q={q}&orderBy=name'
            f'&fields=files(id,name,mimeType,size,thumbnailLink)'
            f'&key={api_key}&pageSize=100'
        )
        try:
            resp = requests.get(url, timeout=10, headers=_HDR)
            if resp.ok:
                results.extend(resp.json().get('files', []))
        except Exception:
            pass

        # Recurse into sub-folders
        if depth < max_depth:
            sub_q = _q(f"'{fid}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false")
            sub_url = (
                f'https://www.googleapis.com/drive/v3/files'
                f'?q={sub_q}&fields=files(id,name)&key={api_key}&pageSize=50'
            )
            try:
                sub_resp = requests.get(sub_url, timeout=10, headers=_HDR)
                if sub_resp.ok:
                    for sub in sub_resp.json().get('files', []):
                        results.extend(_list_folder(sub['id'], depth + 1, max_depth))
            except Exception:
                pass

        return results

    all_files = _list_folder(folder_id)

    audio_files, doc_files = [], []
    for f in all_files:
        mime = f.get('mimeType', '')
        entry = {
            'id':            f['id'],
            'name':          f.get('name', ''),
            'mimeType':      mime,
            'thumbnail_url': f.get('thumbnailLink', ''),
            'preview_url':   f'https://drive.google.com/file/d/{f["id"]}/preview',
            'stream_url':    f'https://www.googleapis.com/drive/v3/files/{f["id"]}?alt=media&key={api_key}',
        }
        if mime in _AUDIO_MIMETYPES:
            audio_files.append(entry)
        elif mime in _DOC_MIMETYPES:
            doc_files.append(entry)

    return audio_files[:50], doc_files[:50]


def community_space_list(request):
    spaces = CommunitySpace.objects.filter(is_public=True).order_by('name')
    return render(request, 'events/community_space_list.html', {'spaces': spaces})


def community_space_profile(request, slug):
    from django.db.models import F as _F
    space = get_object_or_404(CommunitySpace, slug=slug, is_public=True)
    CommunitySpace.objects.filter(pk=space.pk).update(view_count=_F('view_count') + 1)
    space.refresh_from_db(fields=['view_count'])

    can_edit = request.user.is_authenticated and (
        request.user.is_staff or space.claimed_by == request.user
    )
    is_following = (
        request.user.is_authenticated and
        Follow.objects.filter(
            user=request.user,
            target_type=Follow.TYPE_SPACE,
            target_id=space.pk,
        ).exists()
    )
    asks = list(space.asks.exclude(status='fulfilled'))
    audio_files, doc_files = _fetch_space_library(
        space.drive_folder_url, space.show_audio, space.show_docs,
    )
    from .models import SpacePhoto, SpaceUpdate, KofiPost
    from django.utils import timezone as _tz
    photos       = list(space.photos.all()[:30])
    updates      = list(space.updates.all()[:20])
    kofi_posts   = list(space.kofi_posts.filter(is_public=True).order_by('-timestamp', '-created_at')[:20]) if space.kofi else []
    kofi_blog    = [p for p in kofi_posts if p.kofi_type == 'Blog_Post']
    kofi_support = [p for p in kofi_posts if p.kofi_type in ('Donation', 'Subscription', 'Commission', 'Shop_Order')]
    week_ago     = _tz.now() - __import__('datetime').timedelta(days=7)
    kofi_recent  = [p for p in kofi_support if p.timestamp and p.timestamp >= week_ago]

    # Handle new update post (owner only)
    if request.method == 'POST' and request.POST.get('_post_update') == '1' and can_edit:
        body = request.POST.get('update_body', '').strip()
        if body:
            SpaceUpdate.objects.create(space=space, body=body, posted_by=request.user)
        return redirect('community_space_profile', slug=slug)

    return render(request, 'events/community_space_profile.html', {
        'space':         space,
        'can_edit':      can_edit,
        'is_following':  is_following,
        'asks':          asks,
        'audio_files':   audio_files,
        'doc_files':     doc_files,
        'photos':        photos,
        'updates':       updates,
        'kofi_blog':     kofi_blog,
        'kofi_support':  kofi_support,
        'kofi_recent':   kofi_recent,
    })


def community_space_supporters(request, slug):
    from .models import KofiPost
    space = get_object_or_404(CommunitySpace, slug=slug, is_public=True)
    supporters = list(
        space.kofi_posts
        .filter(is_public=True)
        .exclude(kofi_type='Blog_Post')
        .order_by('-timestamp', '-created_at')
    )
    return render(request, 'events/community_space_supporters.html', {
        'space':      space,
        'supporters': supporters,
    })


@login_required(login_url='/login/')
def community_space_edit(request, slug):
    space = get_object_or_404(CommunitySpace, slug=slug)
    if not (request.user.is_staff or space.claimed_by == request.user):
        return redirect('community_space_profile', slug=slug)

    if request.method == 'GET':
        asks = list(space.asks.all())
        return render(request, 'events/community_space_edit.html', {'space': space, 'asks': asks})

    if request.POST.get('_photo_only') == '1':
        from .models import SpacePhoto
        for f in request.FILES.getlist('extra_photos'):
            SpacePhoto.objects.create(space=space, image=f, uploaded_by=request.user)
        messages.success(request, 'Photos added.')
        return redirect('community_space_profile', slug=space.slug)

    if request.POST.get('_asks_only') == '1':
        # Asks-only form — rebuild asks without touching space fields
        parsed = _parse_asks_from_post(request.POST)
        profile_url = f'https://communityplaylist.com/spaces/{space.slug}/'
        new_asks = []
        for d in parsed:
            offering = None
            if d['post_to_board'] and d['ask_type'] == CommunityAsk.TYPE_ITEM:
                offering = _create_iso_offering(d, space.name, space.neighborhood, request.user, profile_url)
            new_asks.append(CommunityAsk(
                community_space=space,
                title=d['title'],
                description=d['description'],
                ask_type=d['ask_type'],
                target_amount=d['target_amount'],
                donation_url=d['donation_url'],
                product_url=d['product_url'],
                product_image_url=d['product_image_url'],
                product_price=d['product_price'],
                board_offering=offering,
                status=d['status'],
                sort_order=d['sort_order'],
            ))
        CommunityAsk.objects.filter(community_space=space).delete()
        CommunityAsk.objects.bulk_create(new_asks)
        messages.success(request, 'Community Asks saved.')
        return redirect('community_space_edit', slug=space.slug)

    import re as _re3, json as _json3
    # Validate brand_color
    bc = request.POST.get('brand_color', '').strip()
    if _re3.fullmatch(r'#[0-9a-fA-F]{6}', bc):
        space.brand_color = bc.lower()
    elif not bc:
        space.brand_color = ''

    for field in ['name', 'bio', 'address', 'neighborhood', 'website',
                  'contact_email', 'instagram', 'bluesky', 'mastodon', 'tiktok', 'kofi',
                  'drive_folder_url', 'sol_wallet', 'donation_url']:
        space.__setattr__(field, request.POST.get(field, '').strip())

    # Auto-generate webhook token on first save if Ko-fi handle is set
    if space.kofi and not space.kofi_token:
        from events.kofi import generate_kofi_token
        space.kofi_token = generate_kofi_token()

    space.show_audio = request.POST.get('show_audio') == '1'
    space.show_docs  = request.POST.get('show_docs')  == '1'

    st = request.POST.get('space_type', '')
    valid_types = [k for k, _ in CommunitySpace.TYPE_CHOICES]
    if st in valid_types:
        space.space_type = st

    if request.FILES.get('photo'):
        space.photo = request.FILES['photo']

    # Custom links — sent as parallel arrays: labels[], urls[], thumbnails[]
    labels     = request.POST.getlist('link_label')
    urls       = request.POST.getlist('link_url')
    thumbnails = request.POST.getlist('link_thumbnail')
    links = []
    for label, url, thumb in zip(labels, urls, thumbnails):
        label = label.strip(); url = url.strip(); thumb = thumb.strip()
        if label and url:
            links.append({'label': label, 'url': url, 'thumbnail_url': thumb})
    space.custom_links = links[:8]

    space.save()

    # Extra photos upload
    from .models import SpacePhoto
    for f in request.FILES.getlist('extra_photos'):
        SpacePhoto.objects.create(space=space, image=f, uploaded_by=request.user)

    # Delete requested photos
    for pid in request.POST.getlist('delete_photo'):
        SpacePhoto.objects.filter(pk=pid, space=space).delete()

    messages.success(request, 'Space updated.')
    return redirect('community_space_edit', slug=space.slug)


# ── Comments toggle API ───────────────────────────────────────────────────────

@login_required(login_url='/login/')
def toggle_comments_api(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    import json as _j
    try:
        data  = _j.loads(request.body)
        model = data.get('model', '')
        pk    = int(data.get('pk', 0))
        allow = bool(data.get('allow', False))
    except (ValueError, TypeError):
        return JsonResponse({'error': 'bad request'}, status=400)

    MODEL_MAP = {
        'artist':   Artist,
        'promoter': PromoterProfile,
        'venue':    Venue,
        'space':    CommunitySpace,
    }
    Klass = MODEL_MAP.get(model)
    if not Klass:
        return JsonResponse({'error': 'unknown model'}, status=400)

    claim_field = {
        'artist':   'claimed_by',
        'promoter': 'claimed_by',
        'venue':    'claimed_by',
        'space':    'claimed_by',
    }[model]
    updated = Klass.objects.filter(pk=pk, **{claim_field: request.user}).update(allow_comments=allow)
    if not updated and not request.user.is_staff:
        return JsonResponse({'error': 'not found or not yours'}, status=403)
    return JsonResponse({'ok': True, 'allow': allow})


# ── About / Support page ──────────────────────────────────────────────────────

def about_page(request):
    from .models import SupportTicket, KofiPost, Event, Artist, Venue, CommunitySpace
    from django.utils import timezone as _tz
    import datetime as _dt

    # Live stats
    stats = {
        'events':  Event.objects.filter(status='approved').count(),
        'artists': Artist.objects.count(),
        'venues':  Venue.objects.filter(active=True).count(),
        'spaces':  CommunitySpace.objects.filter(is_public=True).count(),
    }

    # Site-level Ko-fi supporters (all entity FKs null)
    site_supporters = list(
        KofiPost.objects
        .filter(community_space__isnull=True, artist__isnull=True, promoter__isnull=True, is_public=True)
        .exclude(kofi_type='Blog_Post')
        .order_by('-timestamp', '-created_at')[:50]
    )
    week_ago = _tz.now() - _dt.timedelta(days=7)
    recent_supporters = [p for p in site_supporters if p.timestamp and p.timestamp >= week_ago]

    submitted = False
    error = ''

    if request.method == 'POST':
        ticket_type = request.POST.get('ticket_type', 'other')
        subject     = request.POST.get('subject', '').strip()
        body        = request.POST.get('body', '').strip()
        from_name   = request.POST.get('from_name', '').strip()
        from_email  = request.POST.get('from_email', '').strip()

        if not subject or not body:
            error = 'Please fill in a subject and message.'
        else:
            ticket = SupportTicket.objects.create(
                ticket_type = ticket_type,
                subject     = subject,
                body        = body,
                from_name   = from_name,
                from_email  = from_email,
                user        = request.user if request.user.is_authenticated else None,
            )
            # Discord notification
            _notify_ticket_discord(ticket)
            submitted = True

    return render(request, 'about.html', {
        'stats':             stats,
        'site_supporters':   site_supporters,
        'recent_supporters': recent_supporters,
        'submitted':         submitted,
        'error':             error,
    })


def _notify_ticket_discord(ticket):
    from django.conf import settings
    import json, urllib.request
    webhook = getattr(settings, 'DISCORD_WEBHOOK', '') or getattr(settings, 'DISCORD_WEBHOOK_BOARD', '')
    if not webhook:
        return
    type_labels = {
        'idea': '💡 Idea', 'bug': '🐛 Bug', 'venue': '🏛 Venue',
        'space': '🌱 Space', 'other': '💬 Message',
    }
    label = type_labels.get(ticket.ticket_type, '📩')
    name  = ticket.from_name or (ticket.user.username if ticket.user else 'Anonymous')
    desc  = f'**{label}** from **{name}**'
    if ticket.from_email:
        desc += f' ({ticket.from_email})'
    desc += f'\n\n**{ticket.subject}**\n{ticket.body[:800]}'
    embed = {
        'title':       f'{label} — New Support Ticket #{ticket.pk}',
        'description': desc,
        'color':       0x4caf50,
        'url':         f'https://communityplaylist.com/admin/events/supportticket/{ticket.pk}/change/',
        'footer':      {'text': 'communityplaylist.com/about/'},
    }
    try:
        data = json.dumps({'embeds': [embed]}).encode()
        req  = urllib.request.Request(webhook, data=data, headers={'Content-Type': 'application/json'}, method='POST')
        urllib.request.urlopen(req, timeout=8)
    except Exception as e:
        print(f'[Ticket] Discord notify error: {e}')


# ── Ko-fi webhook ─────────────────────────────────────────────────────────────

from django.views.decorators.csrf import csrf_exempt as _csrf_exempt

@_csrf_exempt
def kofi_webhook_view(request):
    from events.kofi import kofi_webhook
    return kofi_webhook(request)
