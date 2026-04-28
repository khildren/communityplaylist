"""
Ko-fi webhook integration for Community Playlist.

Routing:
  Each CommunitySpace (and in future, Artist/PromoterProfile) has a unique
  `kofi_token` that the owner pastes into their Ko-fi Settings → Webhooks.
  When Ko-fi fires a POST, the payload's `verification_token` is matched
  against stored tokens to route the event to the right entity.

  If no entity matches, the site-level KOFI_VERIFICATION_TOKEN is tried,
  and the event is treated as a CP-level supporter shoutout.

Ko-fi webhook payload reference:
  https://ko-fi.com/manage/webhooks
"""
import json
import secrets
import urllib.request
import urllib.error
from datetime import datetime, timezone as _tz

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST


# ── Helpers ───────────────────────────────────────────────────────────────────

def _settings():
    from django.conf import settings
    return settings


def _parse_ts(ts_str):
    if not ts_str:
        return None
    for fmt in ('%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(ts_str, fmt).replace(tzinfo=_tz.utc)
        except (ValueError, TypeError):
            pass
    return None


def generate_kofi_token():
    return secrets.token_urlsafe(32)


def _discord_post(webhook_url, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        webhook_url, data=data,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
        return True
    except Exception as e:
        print(f'[Ko-fi] Discord error: {e}')
        return False


def _bsky_session():
    try:
        from board.social import _bsky_session as _s
        return _s()
    except Exception:
        return None, None


def _bsky_create(token, did, text, facets=None):
    from board.social import _bsky_create as _c
    return _c(token, did, text, facets=facets)


def _bsky_facets(text, links=(), hashtags=()):
    from board.social import _bsky_facets as _f
    return _f(text, links=list(links), hashtags=list(hashtags))


# ── Entity lookup ─────────────────────────────────────────────────────────────

def _find_entity_by_token(token):
    """Return (kind, obj) for the entity whose kofi_token matches, or (None, None)."""
    if not token:
        return None, None
    from events.models import CommunitySpace
    space = CommunitySpace.objects.filter(kofi_token=token).first()
    if space:
        return 'space', space
    # Future: Artist / PromoterProfile lookup here
    return None, None


# ── Entity-level event handler ────────────────────────────────────────────────

def _handle_entity_event(kind, obj, data):
    """Persist an incoming webhook payload as a KofiPost for a specific entity."""
    from events.models import KofiPost

    # Ko-fi sends 'Shop Order' (space) — normalise for storage
    kofi_type  = data.get('type', 'Donation').replace(' ', '_')
    txn_id     = data.get('message_id') or data.get('kofi_transaction_id') or None
    from_name  = (data.get('from_name') or 'Anonymous').strip()
    message    = (data.get('message') or '').strip()
    url        = (data.get('url') or '').strip()
    is_public  = data.get('is_public', True)
    amount     = str(data.get('amount') or '')
    currency   = (data.get('currency') or '').strip()
    timestamp  = _parse_ts(data.get('timestamp'))

    defaults = {
        'kofi_type': kofi_type,
        'from_name': from_name,
        'message':   message,
        'url':       url,
        'is_public': is_public,
        'amount':    amount,
        'currency':  currency,
        'timestamp': timestamp,
        'raw_data':  data,
    }
    if kind == 'space':
        defaults['community_space'] = obj

    if txn_id:
        KofiPost.objects.get_or_create(kofi_transaction_id=txn_id, defaults=defaults)
    else:
        KofiPost.objects.create(**defaults)

    # Discord shoutout for donation/subscription events
    s = _settings()
    webhook = getattr(s, 'DISCORD_WEBHOOK_BOARD', '')
    if webhook and kofi_type != 'Blog_Post':
        emoji = {'Subscription': '⭐', 'Shop_Order': '🛒', 'Commission': '🎨'}.get(kofi_type, '☕')
        label = {'Subscription': 'subscribed', 'Shop_Order': 'placed a shop order', 'Commission': 'commissioned'}.get(kofi_type, 'supported')
        name  = getattr(obj, 'name', str(obj))
        msg   = f'**{from_name}** just {label} **{name}** on Ko-fi! {emoji}'
        if message and is_public:
            msg += f'\n> {message}'
        _discord_post(webhook, {'embeds': [{'description': msg, 'color': 0xFF5E5B}]})


# ── Webhook receiver ──────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def kofi_webhook(request):
    """
    Receives Ko-fi webhook POSTs.
    Ko-fi sends the payload as a form field named 'data' containing JSON.
    """
    raw = request.POST.get('data') or request.body.decode('utf-8', errors='replace')
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return HttpResponse('bad payload', status=400)

    token = data.get('verification_token', '')

    # 1 — try to route to a specific space / artist / promoter
    kind, obj = _find_entity_by_token(token)
    if kind and obj:
        _handle_entity_event(kind, obj, data)
        return HttpResponse('ok', status=200)

    # 2 — fall back to site-level CP shoutout
    site_token = getattr(_settings(), 'KOFI_VERIFICATION_TOKEN', '')
    if site_token and token == site_token:
        from_name    = data.get('from_name') or 'A supporter'
        message      = (data.get('message') or '').strip()
        support_type = data.get('type', 'Donation')
        kofi_url     = data.get('url', 'https://ko-fi.com/communityplaylist')
        is_public    = data.get('is_public', True)
        # Store as a site-level KofiPost (all entity FKs null)
        _store_site_kofi_post(data)
        _fire_supporter_shoutout(
            from_name=from_name, message=message, support_type=support_type,
            kofi_url=kofi_url, is_public=is_public,
        )
        return HttpResponse('ok', status=200)

    return HttpResponse('forbidden', status=403)


# ── Site-level KofiPost storage ──────────────────────────────────────────────

def _store_site_kofi_post(data):
    """Persist a site-level (CP's own Ko-fi) webhook event with no entity FK."""
    from events.models import KofiPost
    kofi_type = data.get('type', 'Donation').replace(' ', '_')
    txn_id    = data.get('message_id') or data.get('kofi_transaction_id') or None
    defaults  = {
        'kofi_type': kofi_type,
        'from_name': (data.get('from_name') or 'Anonymous').strip(),
        'message':   (data.get('message') or '').strip(),
        'url':       (data.get('url') or '').strip(),
        'is_public': data.get('is_public', True),
        'amount':    str(data.get('amount') or ''),
        'currency':  (data.get('currency') or '').strip(),
        'timestamp': _parse_ts(data.get('timestamp')),
        'raw_data':  data,
    }
    if txn_id:
        KofiPost.objects.get_or_create(kofi_transaction_id=txn_id, defaults=defaults)
    else:
        KofiPost.objects.create(**defaults)


# ── Site-level supporter shoutout ─────────────────────────────────────────────

def _fire_supporter_shoutout(from_name, message, support_type, kofi_url, is_public):
    s = _settings()
    webhook = getattr(s, 'DISCORD_WEBHOOK_BOARD', '')
    cp_kofi = getattr(s, 'KOFI_PAGE', 'communityplaylist')
    cp_url  = f'https://ko-fi.com/{cp_kofi}'

    emoji = {'Subscription': '⭐', 'Shop Order': '🛒'}.get(support_type, '☕')
    label = {'Subscription': 'subscribed', 'Shop Order': 'placed a shop order'}.get(support_type, 'bought a coffee')

    if webhook:
        desc = f'**{from_name}** just {label} on Ko-fi! {emoji}'
        if message and is_public:
            desc += f'\n> {message}'
        desc += f'\n\n[Support Community Playlist]({cp_url})'
        _discord_post(webhook, {'embeds': [{
            'title': f'{emoji} Ko-fi Support — Thank You!',
            'description': desc,
            'color': 0xFF5E5B,
            'url': cp_url,
        }]})

    token, did = _bsky_session()
    if token:
        pub_msg = f' "{message}"' if (message and is_public and len(message) < 120) else ''
        text = (
            f'{emoji} {from_name} just supported Community Playlist on Ko-fi!{pub_msg}\n\n'
            f'If you love free local PDX music listings, a coffee helps keep us running:\n'
            f'{cp_url}\n\n#PDX #Portland #CommunityPlaylist'
        )[:300]
        facets = _bsky_facets(text, links=[cp_url], hashtags=['#PDX', '#Portland', '#CommunityPlaylist'])
        try:
            _bsky_create(token, did, text, facets=facets)
        except Exception as e:
            print(f'[Ko-fi] Bluesky shoutout error: {e}')


# ── Daily broadcast ───────────────────────────────────────────────────────────

def kofi_daily_broadcast(dry_run=False):
    s = _settings()
    webhook  = getattr(s, 'DISCORD_WEBHOOK_KOFI', '') or getattr(s, 'DISCORD_WEBHOOK_BOARD', '')
    cp_kofi  = getattr(s, 'KOFI_PAGE', 'communityplaylist')
    cp_url   = f'https://ko-fi.com/{cp_kofi}'

    from django.utils import timezone
    day = timezone.now().strftime('%A')

    discord_text = (
        f'☕ **Happy {day}, PDX!**\n\nCommunity Playlist is free, ad-free, and community-run. '
        f'If you find value in local event listings, mixes, and artist pages — a Ko-fi goes a long way.\n\n'
        f'**[Buy us a coffee ☕]({cp_url})**\n\nEvery supporter gets a shoutout. Thank you! 🙏'
    )
    bsky_text = (
        f'☕ Happy {day}, PDX!\n\nCommunity Playlist is free, ad-free, and run by locals. '
        f'If you enjoy free PDX event listings + artist mixes, a Ko-fi keeps the lights on.\n\n'
        f'{cp_url}\n\n#PDX #Portland #PDXEvents #CommunityPlaylist'
    )[:300]

    discord_ok = bluesky_ok = False

    if dry_run:
        print(f'[DRY] Discord:\n{discord_text}\n')
        print(f'[DRY] Bluesky:\n{bsky_text}\n')
        return True, True

    if webhook:
        discord_ok = _discord_post(webhook, {'embeds': [{
            'title': '☕ Support Community Playlist on Ko-fi',
            'description': discord_text,
            'color': 0xFF5E5B,
            'url': cp_url,
        }]})

    token, did = _bsky_session()
    if token:
        facets = _bsky_facets(bsky_text, links=[cp_url],
                              hashtags=['#PDX', '#Portland', '#PDXEvents', '#CommunityPlaylist'])
        try:
            _bsky_create(token, did, bsky_text, facets=facets)
            bluesky_ok = True
        except Exception as e:
            print(f'[Ko-fi] broadcast Bluesky error: {e}')

    return discord_ok, bluesky_ok
