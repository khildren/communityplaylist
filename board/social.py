"""
Social auto-posting for Community Playlist.

Handles Bluesky (AT Protocol, no external deps) and Discord (webhooks)
for board topics, Free & Trade offerings, and new approved events.

Entry points:
  post_topic(topic)         — board topic → both platforms
  post_offering(offering)   — Free & Trade item → both platforms
  post_event_discord(event) — new approved event → Discord embed
  bluesky_events_digest()   — called by bluesky_digest management command;
                              handles 27-post split-by-category logic
"""
import json
import re
import time
import unicodedata
import urllib.request
import urllib.error
from datetime import datetime, timezone as dt_tz

BSKY_HOST = 'https://bsky.social'
CP_BASE   = 'https://communityplaylist.com'
LOGO      = 'https://hihi.communityplaylist.com/files/timeline_files/store_file6809b5ed4135d-community_playlist_site_logo_2025.png'

# Discord embed colors by content type
COLORS = {
    'general':   0x2a2a2a,
    'aid':       0x4caf50,
    'announce':  0xddaa33,
    'question':  0x8888ff,
    'offer':     0x4caf50,
    'give':      0x4caf50,
    'trade':     0x6699dd,
    'iso':       0xddaa33,
    'event':     0xff6b35,
}

# Category → filtered homepage link + hashtag
EVENT_CATS = {
    'music':  ('/?cat=music',  '#PDXMusic'),
    'arts':   ('/?cat=arts',   '#PDXArts'),
    'food':   ('/?cat=food',   '#PDXFood'),
    'bike':   ('/?cat=bike',   '#PDXBike'),
    'fund':   ('/?cat=fund',   '#PDXFundraiser'),
    'hybrid': ('/?cat=hybrid', '#PDXEvents'),
    '':       ('/',            '#PDXEvents'),
}

BOARD_TAGS = {
    'general':  '#PDXCommunity #Portland',
    'aid':      '#PDXAid #MutualAid #Portland',
    'announce': '#PDX #Portland',
    'question': '#PDXCommunity #Portland',
    'offer':    '#PDXFree #BuyNothingPDX #Portland',
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slugify_tag(text):
    """'Living Häus Beer Co' → '#LivingHausBeerCo'"""
    norm = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    norm = re.sub(r'[^a-zA-Z0-9 ]', '', norm)
    return '#' + ''.join(w.capitalize() for w in norm.split() if w)


def _title_tags(title, max_words=3):
    """'Techno Night at the Crystal' → '#TechnoNight #Crystal'  (skip short/common words)"""
    skip = {'a','an','the','at','in','on','of','and','or','for','to','with','by','from','&'}
    words = [w for w in re.findall(r"[a-zA-Z0-9']+", title) if w.lower() not in skip and len(w) > 2]
    return ' '.join(_slugify_tag(w) for w in words[:max_words])


def _venue_tag(location):
    """Extract venue name (first segment before comma) → hashtag."""
    if not location:
        return ''
    name = location.split(',')[0].strip()
    return _slugify_tag(name) if name else ''


# ── Bluesky low-level ─────────────────────────────────────────────────────────

def _bsky_post(path, payload, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(
        f'{BSKY_HOST}/xrpc/{path}',
        data=json.dumps(payload).encode(),
        headers=headers,
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _bsky_session():
    from django.conf import settings
    handle   = getattr(settings, 'BLUESKY_HANDLE', '')
    password = getattr(settings, 'BLUESKY_APP_PASSWORD', '')
    if not handle or not password:
        return None, None
    data = _bsky_post('com.atproto.server.createSession',
                      {'identifier': handle, 'password': password})
    return data['accessJwt'], data['did']


def _bsky_upload_blob(image_url, token):
    try:
        req = urllib.request.Request(image_url,
            headers={'User-Agent': 'CommunityPlaylist/1.0 (communityplaylist.com)'})
        with urllib.request.urlopen(req, timeout=15) as r:
            img_data = r.read()
            ctype = r.headers.get('Content-Type', 'image/jpeg').split(';')[0]
        upload = urllib.request.Request(
            f'{BSKY_HOST}/xrpc/com.atproto.repo.uploadBlob',
            data=img_data,
            headers={'Content-Type': ctype, 'Authorization': f'Bearer {token}'},
            method='POST',
        )
        with urllib.request.urlopen(upload, timeout=20) as r:
            return json.loads(r.read()).get('blob')
    except Exception:
        return None


def _bsky_facets(text, links=(), hashtags=()):
    """
    links    = list of url strings that appear literally in text
    hashtags = list of '#Tag' strings that appear literally in text
    """
    tb = text.encode('utf-8')
    facets = []
    for url in links:
        b = url.encode('utf-8')
        idx = tb.find(b)
        if idx < 0:
            continue
        facets.append({
            '$type': 'app.bsky.richtext.facet',
            'index': {'byteStart': idx, 'byteEnd': idx + len(b)},
            'features': [{'$type': 'app.bsky.richtext.facet#link', 'uri': url}],
        })
    for tag in hashtags:
        b = tag.encode('utf-8')
        idx = tb.find(b)
        if idx < 0:
            continue
        facets.append({
            '$type': 'app.bsky.richtext.facet',
            'index': {'byteStart': idx, 'byteEnd': idx + len(b)},
            'features': [{'$type': 'app.bsky.richtext.facet#tag',
                          'tag': tag.lstrip('#')}],
        })
    return facets or None


def _bsky_create(token, did, text, facets=None, embed=None, reply_ref=None):
    record = {
        '$type':     'app.bsky.feed.post',
        'text':      text[:300],
        'createdAt': datetime.now(dt_tz.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z'),
        'langs':     ['en-US'],
    }
    if facets:
        record['facets'] = facets
    if embed:
        record['embed'] = embed
    if reply_ref:
        record['reply'] = reply_ref
    result = _bsky_post('com.atproto.repo.createRecord', {
        'repo': did, 'collection': 'app.bsky.feed.post', 'record': record,
    }, token=token)
    return result.get('uri', ''), result.get('cid', '')


# ── Discord low-level ─────────────────────────────────────────────────────────

def _discord_send(webhook_url, payload):
    if not webhook_url:
        return False
    try:
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=10):
            return True
    except Exception as e:
        print(f'[Discord] send failed: {e}')
        return False


# ── Board topic posting ────────────────────────────────────────────────────────

def post_topic(topic):
    """Post a board topic to both Bluesky and Discord. Returns (bsky_ok, discord_ok)."""
    return (
        _post_topic_bluesky(topic),
        _post_topic_discord(topic),
    )


def _post_topic_bluesky(topic):
    try:
        token, did = _bsky_session()
        if not token:
            return False

        url  = f'{CP_BASE}{topic.get_absolute_url()}'
        tags = BOARD_TAGS.get(topic.category, '#PDXCommunity #Portland')
        body_preview = (topic.body or '')[:180].strip()
        if len(topic.body or '') > 180:
            body_preview += '…'

        tag_list = tags.split()
        text = f'💬 {topic.title}\n\n{body_preview}\n\n{url}\n\n{tags}'
        text = text[:300]

        facets = _bsky_facets(text, links=[url], hashtags=tag_list)

        embed = {
            '$type': 'app.bsky.embed.external',
            'external': {
                'uri':         url,
                'title':       topic.title,
                'description': (topic.body or '')[:200],
            },
        }
        _bsky_create(token, did, text, facets=facets, embed=embed)
        return True
    except Exception as e:
        print(f'[Bluesky] topic post failed: {e}')
        return False


def _post_topic_discord(topic):
    from django.conf import settings
    webhook = getattr(settings, 'DISCORD_WEBHOOK_BOARD', '')
    if not webhook:
        return False

    cat_labels = {
        'general': 'General', 'aid': '🌹 Aid & Mutual Aid',
        'announce': '📢 Announcement', 'question': '❓ Question',
        'offer': '🎁 Free & Trade',
    }
    url   = f'{CP_BASE}{topic.get_absolute_url()}'
    color = COLORS.get(topic.category, 0x2a2a2a)
    desc  = (topic.body or '')[:400]

    payload = {
        # thread_name creates a new Forum thread when the webhook targets a Forum channel;
        # text channel webhooks silently ignore it.
        'thread_name': topic.title[:100],
        'embeds': [{
            'title':       topic.title,
            'url':         url,
            'description': desc,
            'color':       color,
            'author':      {'name': f'Community Board — {cat_labels.get(topic.category, topic.category)}'},
            'footer':      {'text': f'Posted by {topic.author_name} · communityplaylist.com',
                            'icon_url': LOGO},
            'timestamp':   topic.created_at.strftime('%Y-%m-%dT%H:%M:%SZ'),
        }],
    }
    return _discord_send(webhook, payload)


# ── Free & Trade offering posting ─────────────────────────────────────────────

def post_offering(offering):
    """Post a Free & Trade offering to both platforms."""
    return (
        _post_offering_bluesky(offering),
        _post_offering_discord(offering),
    )


def _post_offering_bluesky(offering):
    try:
        token, did = _bsky_session()
        if not token:
            return False

        url  = f'{CP_BASE}{offering.get_absolute_url()}'
        hood = f' · {offering.neighborhood.name}' if offering.neighborhood else ''
        cat_icons = {'give': '🎁 FREE', 'trade': '🔄 TRADE', 'iso': '🔍 ISO'}
        cat_label = cat_icons.get(offering.category, '🎁')
        tags = '#PDXFree #BuyNothingPDX #Portland'
        if offering.neighborhood:
            tags += f' {_slugify_tag(offering.neighborhood.name)}'

        body_preview = (offering.body or '')[:140].strip()
        tag_list = [t for t in tags.split() if t.startswith('#')]

        text = f'{cat_label} — {offering.title}{hood}\n\n{body_preview}\n\n{url}\n\n{tags}'
        text = text[:300]

        facets = _bsky_facets(text, links=[url], hashtags=tag_list)
        thumb = None
        if offering.photo:
            thumb = _bsky_upload_blob(f'{CP_BASE}{offering.photo.url}', token)

        embed = {
            '$type': 'app.bsky.embed.external',
            'external': {
                'uri':         url,
                'title':       f'{cat_label} — {offering.title}',
                'description': (offering.body or '')[:200],
            },
        }
        if thumb:
            embed['external']['thumb'] = thumb

        _bsky_create(token, did, text, facets=facets, embed=embed)
        return True
    except Exception as e:
        print(f'[Bluesky] offering post failed: {e}')
        return False


def _post_offering_discord(offering):
    from django.conf import settings
    webhook = getattr(settings, 'DISCORD_WEBHOOK_BOARD', '')
    if not webhook:
        return False

    url   = f'{CP_BASE}{offering.get_absolute_url()}'
    color = COLORS.get(offering.category, 0x4caf50)
    cat_labels = {'give': '🎁 Free — Take It', 'trade': '🔄 Trade / Swap', 'iso': '🔍 In Search Of'}
    hood  = offering.neighborhood.name if offering.neighborhood else 'Portland'
    img   = f'{CP_BASE}{offering.photo.url}' if offering.photo else None

    embed = {
        'title':       f'{cat_labels.get(offering.category, "Offering")} — {offering.title}',
        'url':         url,
        'description': (offering.body or '')[:400],
        'color':       color,
        'author':      {'name': f'🎁 Free & Trade — {hood}'},
        'footer':      {'text': f'Posted by {offering.author_name} · communityplaylist.com',
                        'icon_url': LOGO},
        'timestamp':   offering.created_at.strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    if img:
        embed['thumbnail'] = {'url': img}
    if offering.contact_hint:
        embed['fields'] = [{'name': '📬 How to connect', 'value': offering.contact_hint, 'inline': False}]

    return _discord_send(webhook, {'embeds': [embed]})


# ── New approved event → Discord ──────────────────────────────────────────────

def post_event_discord(event):
    """Rich Discord embed for a newly approved event."""
    from django.conf import settings
    from django.utils.timezone import localtime
    webhook = getattr(settings, 'DISCORD_WEBHOOK_EVENTS', '')
    if not webhook:
        return False

    url    = f'{CP_BASE}/events/{event.slug}/'
    genres = ', '.join(event.genres.values_list('name', flat=True)[:4]) or 'various'
    start  = localtime(event.start_date).strftime('%a %b %-d @ %-I:%M %p')
    cost   = 'FREE' if event.is_free else (event.price_info or 'Paid')
    img    = f'{CP_BASE}{event.photo.url}' if event.photo else LOGO
    vtag   = _venue_tag(event.location)
    ttag   = _title_tags(event.title)
    cat_path, cat_hashtag = EVENT_CATS.get(event.category or '', EVENT_CATS[''])
    hood   = getattr(event, 'neighborhood', '') or ''

    embed = {
        'title':       event.title,
        'url':         url,
        'description': (event.description or '')[:300],
        'color':       0xff6b35,
        'thumbnail':   {'url': img},
        'fields': [
            {'name': '📅 When',    'value': start,                'inline': True},
            {'name': '📍 Where',   'value': event.location[:80],  'inline': True},
            {'name': '🎵 Genre',   'value': genres,               'inline': True},
            {'name': '💰 Cost',    'value': cost,                 'inline': True},
        ],
        'author':  {'name': '🌹 New Event — Community Playlist'},
        'footer':  {'text': f'{ttag} {vtag} {cat_hashtag} #PDX\ncommunityplaylist.com',
                    'icon_url': LOGO},
        'timestamp': event.created_at.strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    if hood:
        embed['fields'].append({'name': '🏘 Neighborhood', 'value': hood, 'inline': True})
    return _discord_send(webhook, {'embeds': [embed]})


# ── Discord Scheduled Events (native Events tab, requires bot token) ──────────

def create_discord_scheduled_event(event):
    """
    Creates a native Discord Scheduled Event (appears in the Events tab).
    Requires DISCORD_BOT_TOKEN and DISCORD_GUILD_ID in settings.
    Bot must have MANAGE_EVENTS permission in the server.
    Returns True on success.
    """
    from django.conf import settings
    token    = getattr(settings, 'DISCORD_BOT_TOKEN', '')
    guild_id = getattr(settings, 'DISCORD_GUILD_ID', '')
    if not token or not guild_id:
        return False

    try:
        url   = f'{CP_BASE}/events/{event.slug}/'
        desc  = (event.description or '')[:1000]
        if url not in desc:
            desc = f'{desc}\n\n{url}'.strip()

        # Discord requires start_time in ISO8601; end_time optional but recommended
        start_iso = event.start_date.strftime('%Y-%m-%dT%H:%M:%S+00:00')
        end_iso   = None
        if hasattr(event, 'end_date') and event.end_date:
            end_iso = event.end_date.strftime('%Y-%m-%dT%H:%M:%S+00:00')

        payload = {
            'name':                event.title[:100],
            'privacy_level':       2,          # GUILD_ONLY (required value)
            'scheduled_start_time': start_iso,
            'description':         desc[:1000],
            'entity_type':         3,          # EXTERNAL (location-based, not a voice channel)
            'entity_metadata':     {'location': (event.location or 'Portland, OR')[:100]},
        }
        if end_iso:
            payload['scheduled_end_time'] = end_iso

        # Optionally attach event cover image
        if event.photo:
            try:
                img_url = f'{CP_BASE}{event.photo.url}'
                req = urllib.request.Request(img_url,
                    headers={'User-Agent': 'CommunityPlaylist/1.0'})
                with urllib.request.urlopen(req, timeout=10) as r:
                    import base64
                    ctype = r.headers.get('Content-Type', 'image/jpeg').split(';')[0]
                    img_b64 = base64.b64encode(r.read()).decode()
                payload['image'] = f'data:{ctype};base64,{img_b64}'
            except Exception:
                pass  # image optional, skip on error

        api_url = f'https://discord.com/api/v10/guilds/{guild_id}/scheduled-events'
        req = urllib.request.Request(
            api_url,
            data=json.dumps(payload).encode(),
            headers={
                'Content-Type':  'application/json',
                'Authorization': f'Bot {token}',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
            return bool(result.get('id'))
    except Exception as e:
        print(f'[Discord] scheduled event creation failed: {e}')
        return False


# ── Events digest helpers (used by bluesky_digest command) ────────────────────

def events_by_category(events):
    """Split a queryset into dict of {category: [events]}."""
    buckets = {}
    for e in events:
        cat = e.category or 'music'
        buckets.setdefault(cat, []).append(e)
    return buckets


def build_event_batch_posts(events, daily_limit=27):
    """
    Given a queryset of today's events, return a list of (header_text, [event_texts]) tuples.
    If total <= daily_limit: one batch.
    If total > daily_limit: split by category, one batch per category.
    Each batch is posted as a Bluesky thread (header + per-event replies).
    """
    from django.utils.timezone import localtime

    cat_labels = {
        'music': '🎵 Music',
        'arts':  '🎨 Arts & Comedy',
        'food':  '🍎 Food & Community',
        'bike':  '🚲 Bike',
        'fund':  '💛 Fundraisers',
        'hybrid':'✦ Hybrid',
        '':      '🌹 Events',
    }

    event_list = list(events)
    total = len(event_list)

    if total <= daily_limit:
        buckets = {'all': event_list}
    else:
        buckets = events_by_category(event_list)

    batches = []
    for cat, cat_events in buckets.items():
        if not cat_events:
            continue
        if cat == 'all':
            label = '🌹 Today in Portland'
            cat_path, cat_tag = '/', '#PDXEvents'
        else:
            label = f'{cat_labels.get(cat, "Events")} Tonight'
            cat_path, cat_tag = EVENT_CATS.get(cat, ('/', '#PDXEvents'))

        link = f'{CP_BASE}{cat_path}'
        from django.utils.timezone import localtime as _lt
        from django.utils import timezone as _tz
        date_str = _lt(_tz.now()).strftime('%a %b %-d')
        header = f'{label} — {date_str}\n{link}\n\n{cat_tag} #Portland #PDX'

        event_texts = []
        for e in cat_events:
            start  = _lt(e.start_date).strftime('%-I:%M %p')
            genres = ', '.join(e.genres.values_list('name', flat=True)[:2]) or 'various'
            cost   = 'FREE' if e.is_free else (e.price_info or 'Paid')
            vtag   = _venue_tag(e.location)
            ttag   = _title_tags(e.title, max_words=2)
            eurl   = f'{CP_BASE}/events/{e.slug}/'
            loc    = e.location[:50]
            text   = (
                f'{e.title}\n'
                f'📅 {start}  📍 {loc}\n'
                f'🎵 {genres}  {cost}\n'
                f'{vtag}  {ttag}\n{eurl}'
            )
            event_texts.append((text[:300], eurl, [vtag, ttag]))

        batches.append((header, link, event_texts))

    return batches
