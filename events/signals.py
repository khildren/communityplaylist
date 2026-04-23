"""
Event signals — auto-parse artists and build stubs when events are approved.
Also auto-links new events to matching RecurringEvent records by title.
"""
import re
import unicodedata
from collections import Counter
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

# ── Reuse filter logic from backfill_artist_links ────────────────────────────

SKIP_EXACT = {
    'pdx', 'portland', 'oregon', 'or', 'presents', 'live', 'session',
    'night', 'morning', 'evening', 'day', 'room', 'stage', 'floor',
    'more', 'tba', 'tbd', 'special', 'guests', 'guest', 'various',
    'artists', 'artist', 'local', 'djs', 'bands', 'open', 'mic',  # 'dj' intentionally absent — "DJ Name" = artist
    'the', 'a', 'an', 'and', 'with', 'feat', 'vs', 'b2b',
    'beer', 'wine', 'yoga', 'food', 'music', 'dance', 'comedy', 'party',
    'show', 'event', 'fest', 'festival', 'tour', 'release',
    'friends', 'family', 'community', 'happy', 'hour',
}
SKIP_PHRASES = {'special guests', 'special guest', 'and friends', 'various artists'}
GENERIC_SINGLES = {'girls', 'gays', 'theys', 'they', 'queer', 'special', 'guests', 'guest'}
EVENT_TYPE_RE = re.compile(
    r'\b(club|swap|run|tasting|trivia|bingo|karaoke|chess|game night|game|'
    r'tattoo|craft|scribe|meditation|adoption|party|show|nights?|dance night|'
    r'wednesday|monday|tuesday|thursday|friday|saturday|sunday|'
    r'weekly|monthly|annual|season|series|presents)\b',
    re.IGNORECASE,
)
STARTS_CAPITAL_RE = re.compile(r'^[A-Z]')


def _is_plausible_artist(name):
    if not name or len(name) < 2:
        return False
    lower = name.lower()
    if lower in SKIP_EXACT or lower in SKIP_PHRASES or lower in GENERIC_SINGLES:
        return False
    if re.match(r'^\d+$', name) or len(name) > 50:
        return False
    if not STARTS_CAPITAL_RE.match(name):
        return False
    if '(' in name or ')' in name:
        return False
    if EVENT_TYPE_RE.search(name):
        return False
    return True


def _build_stub(artist):
    """Fill geo/auto_bio for a freshly-linked artist if they now qualify (≥2 events)."""
    from events.models import Event

    events_qs = Event.objects.filter(status='approved', artists__id=artist.pk)
    count = events_qs.count()
    if count < 2:
        return   # not enough history yet

    lats, lngs, neighborhoods, venue_names = [], [], [], []
    for ev in events_qs:
        if ev.latitude and ev.longitude:
            lats.append(ev.latitude)
            lngs.append(ev.longitude)
        if ev.neighborhood:
            neighborhoods.append(ev.neighborhood.strip())
        vname = (ev.location or '').split(',')[0].strip()
        if vname:
            venue_names.append(vname)

    avg_lat   = sum(lats) / len(lats) if lats else None
    avg_lng   = sum(lngs) / len(lngs) if lngs else None
    home_hood = Counter(neighborhoods).most_common(1)[0][0] if neighborhoods else ''
    top_venues = [v for v, _ in Counter(venue_names).most_common(3)]
    venue_str  = ', '.join(top_venues) if top_venues else 'local venues'
    hood_str   = f' in the {home_hood}' if home_hood else ''

    artist.is_stub = True
    if avg_lat and not artist.latitude:
        artist.latitude  = avg_lat
        artist.longitude = avg_lng
    if home_hood and not artist.home_neighborhood:
        artist.home_neighborhood = home_hood
    if not artist.city:
        artist.city = 'Portland, OR'
    artist.auto_bio = (
        f'{artist.name} has performed at {count} events on CommunityPlaylist'
        f'{hood_str}, with appearances at {venue_str}. '
        f'This profile was auto-generated from event history — '
        f'is this you? Claim it to add your bio, links, and music.'
    )
    artist.last_enriched_at = timezone.now()
    artist.save()


@receiver(post_save, sender='events.Event')
def event_approved_parse_artists(sender, instance, **kwargs):
    """On event approval, parse artist names and create/link stub Artist records."""
    if instance.status != 'approved':
        return
    if instance.category not in ('music', 'hybrid', ''):
        return

    from events.views import _parse_lineup_from_title
    from events.models import Artist

    parsed = _parse_lineup_from_title(instance.title)
    existing_ids = set(instance.artists.values_list('id', flat=True))

    # Merge artists + any "crew" names that are actually solo DJ artists
    candidates = parsed.get('artists', []) + [
        n for n in parsed.get('crews', [])
        if re.match(r'^DJ\s+\S', n, re.IGNORECASE)
    ]

    for raw in candidates:
        name = raw.strip().strip('.').strip(',')
        if not _is_plausible_artist(name):
            continue

        artist = Artist.objects.filter(name__iexact=name).first()
        if not artist:
            artist = Artist(name=name)
            artist.save()

        if artist.pk not in existing_ids:
            instance.artists.add(artist)
            existing_ids.add(artist.pk)

        # If unclaimed and no real profile, refresh stub data
        if not artist.claimed_by_id and not any([artist.bio, artist.website, artist.instagram]):
            _build_stub(artist)


# ── Auto-link new events to RecurringEvent by title ──────────────────────────

_RECURRING_CACHE = {}  # {normalised_title: RecurringEvent pk} — refreshed per process

def _norm(title):
    """Normalise for fuzzy title matching: lowercase, strip accents + punctuation."""
    t = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9 ]', '', t.lower()).strip()


def _get_recurring_map():
    """Return {normalised_title: RecurringEvent} — rebuilt whenever the cache is empty."""
    from events.models import RecurringEvent
    if not _RECURRING_CACHE:
        for r in RecurringEvent.objects.all():
            _RECURRING_CACHE[_norm(r.title)] = r
    return _RECURRING_CACHE


def invalidate_recurring_cache(**kwargs):
    """Clear cache when a RecurringEvent is saved or deleted."""
    _RECURRING_CACHE.clear()


# Wire cache invalidation to RecurringEvent changes
from django.db.models.signals import post_save, post_delete
from events.models import RecurringEvent as _RE
post_save.connect(invalidate_recurring_cache, sender=_RE)
post_delete.connect(invalidate_recurring_cache, sender=_RE)


@receiver(post_save, sender='events.Event')
def auto_link_recurring(sender, instance, created, **kwargs):
    """
    When a new Event is saved without a recurring_event link,
    check if its title matches a RecurringEvent and link it automatically.
    Only runs on creation to avoid overriding manual assignments.
    """
    if not created:
        return
    if instance.recurring_event_id:
        return  # already linked

    rmap = _get_recurring_map()
    key = _norm(instance.title)
    match = rmap.get(key)

    if match:
        # Use update() to avoid re-triggering signals
        sender.objects.filter(pk=instance.pk).update(recurring_event=match)
