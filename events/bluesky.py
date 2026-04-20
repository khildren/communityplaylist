"""
Bluesky AT Protocol poster for Community Playlist.

Posts a rich link card when a music event is approved.
No external dependencies — pure urllib + json.

Usage:
    from events.bluesky import post_event_to_bluesky
    post_event_to_bluesky(event)
"""
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone

BSKY_HOST = 'https://bsky.social'


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


def _create_session(handle, app_password):
    """Auth — returns (access_jwt, did)."""
    data = _bsky_post('com.atproto.server.createSession', {
        'identifier': handle,
        'password':   app_password,
    })
    return data['accessJwt'], data['did']


def _upload_blob(image_url, token):
    """Download an image and upload it to Bluesky as a blob. Returns blob dict or None."""
    try:
        img_req = urllib.request.Request(
            image_url,
            headers={'User-Agent': 'CommunityPlaylist/1.0 (communityplaylist.com)'},
        )
        with urllib.request.urlopen(img_req, timeout=15) as r:
            img_data = r.read()
            content_type = r.headers.get('Content-Type', 'image/jpeg').split(';')[0]

        upload_req = urllib.request.Request(
            f'{BSKY_HOST}/xrpc/com.atproto.repo.uploadBlob',
            data=img_data,
            headers={
                'Content-Type': content_type,
                'Authorization': f'Bearer {token}',
            },
            method='POST',
        )
        with urllib.request.urlopen(upload_req, timeout=20) as r:
            return json.loads(r.read()).get('blob')
    except Exception:
        return None


def _build_facets(text, url):
    """Build a link facet so the URL in the post body is clickable."""
    encoded = text.encode('utf-8')
    start = encoded.find(url.encode('utf-8'))
    if start == -1:
        return []
    return [{
        '$type': 'app.bsky.richtext.facet',
        'index': {'byteStart': start, 'byteEnd': start + len(url.encode('utf-8'))},
        'features': [{'$type': 'app.bsky.richtext.facet#link', 'uri': url}],
    }]


def post_event_to_bluesky(event):
    """
    Post a link card for `event` to Bluesky.
    Silently returns False on any error so it never breaks the approval flow.
    """
    from django.conf import settings
    from django.utils import timezone as dj_tz

    handle   = getattr(settings, 'BLUESKY_HANDLE', '')
    password = getattr(settings, 'BLUESKY_APP_PASSWORD', '')
    if not handle or not password:
        return False

    # Only post music / hybrid events
    if event.category not in ('music', 'hybrid', ''):
        return False

    try:
        token, did = _create_session(handle, password)
    except Exception as e:
        print(f'[Bluesky] auth failed: {e}')
        return False

    try:
        # ── Build post text (300 char limit) ─────────────────────────────────
        start_local = event.start_date.astimezone(dj_tz.get_current_timezone())
        date_str    = start_local.strftime('%a %b %-d @ %-I:%M %p')
        genres      = ', '.join(event.genres.values_list('name', flat=True)[:3])
        location    = (event.location or 'Portland, OR')[:60]
        event_url   = f'https://communityplaylist.com/events/{event.slug}/'

        lines = [f'📅 {event.title}', f'🕐 {date_str}  📍 {location}']
        if genres:
            lines.append(f'🎵 {genres}')
        lines.append(f'🔗 {event_url}')
        lines.append('#PDX #Portland #PDXEvents')
        text = '\n'.join(lines)[:300]

        # ── Build external embed (link card) ─────────────────────────────────
        desc = (event.description or '')[:200].strip()
        if not desc:
            desc = f'{date_str} · {location}'

        thumb = None
        if event.photo:
            photo_url = f'https://communityplaylist.com{event.photo.url}'
            thumb = _upload_blob(photo_url, token)

        embed = {
            '$type': 'app.bsky.embed.external',
            'external': {
                'uri':         event_url,
                'title':       event.title,
                'description': desc,
            },
        }
        if thumb:
            embed['external']['thumb'] = thumb

        # ── Create the post record ────────────────────────────────────────────
        record = {
            '$type':     'app.bsky.feed.post',
            'text':      text,
            'facets':    _build_facets(text, event_url),
            'embed':     embed,
            'createdAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'langs':     ['en'],
        }

        result = _bsky_post('com.atproto.repo.createRecord', {
            'repo':       did,
            'collection': 'app.bsky.feed.post',
            'record':     record,
        }, token=token)

        uri = result.get('uri', '')
        print(f'[Bluesky] posted: {uri}')
        return True

    except Exception as e:
        print(f'[Bluesky] post failed: {e}')
        return False
