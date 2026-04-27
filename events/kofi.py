"""
Ko-fi integration for Community Playlist.

Two entry points:

  handle_kofi_webhook(request) → HttpResponse
      Called by the /webhooks/kofi/ view. Verifies the payload, matches the
      Ko-fi creator to a CP artist or promoter, then fires a Discord shoutout
      and a Bluesky thank-you post.

  kofi_daily_broadcast()
      Called by the kofi_broadcast management command (run daily via cron).
      Posts a "support us on Ko-fi" message to both Bluesky and Discord,
      optionally listing the day's new supporters (names only, no amounts).

Settings (all optional — feature silently disabled if missing):
  KOFI_VERIFICATION_TOKEN  — from ko-fi.com/manage/api (the KF_API_... key)
  DISCORD_WEBHOOK_KOFI     — dedicated Discord webhook URL for Ko-fi posts
  BLUESKY_HANDLE / BLUESKY_APP_PASSWORD — reuses existing Bluesky creds

Ko-fi webhook payload reference:
  https://ko-fi.com/manage/webhooks  (docs linked from the API page)
"""
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST


# ── Helpers ───────────────────────────────────────────────────────────────────

def _settings():
    from django.conf import settings
    return settings


def _verify_token(payload_token):
    token = getattr(_settings(), 'KOFI_VERIFICATION_TOKEN', '')
    return token and payload_token == token


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
    from board.social import _bsky_session as _s
    return _s()


def _bsky_create(token, did, text, facets=None):
    from board.social import _bsky_create as _c
    return _c(token, did, text, facets=facets)


def _bsky_facets(text, links=(), hashtags=()):
    from board.social import _bsky_facets as _f
    return _f(text, links=list(links), hashtags=list(hashtags))


def _find_creator(kofi_username):
    """Return (type, obj) matching kofi_username, or (None, None)."""
    from events.models import Artist, PromoterProfile
    username = kofi_username.lower().strip()
    artist = Artist.objects.filter(kofi__iexact=username).first()
    if artist:
        return 'artist', artist
    promoter = PromoterProfile.objects.filter(kofi__iexact=username).first()
    if promoter:
        return 'promoter', promoter
    return None, None


# ── Webhook receiver ──────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def kofi_webhook(request):
    """
    Receives Ko-fi webhook POSTs. Ko-fi sends the payload as a form field
    named 'data' containing JSON.
    """
    raw = request.POST.get('data') or request.body.decode('utf-8', errors='replace')
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return HttpResponse('bad payload', status=400)

    if not _verify_token(data.get('verification_token', '')):
        return HttpResponse('forbidden', status=403)

    support_type  = data.get('type', 'Donation')          # Donation / Subscription / Shop Order
    from_name     = data.get('from_name') or 'A supporter'
    message       = (data.get('message') or '').strip()
    kofi_url      = data.get('url', 'https://ko-fi.com/communityplaylist')
    is_public     = data.get('is_public', True)
    kofi_creator  = (data.get('kofi_transaction_id') or '')  # not the creator username

    # Ko-fi doesn't send the page-owner's username in the webhook — the webhook
    # is tied to your account, so it's always CP's own page unless you route
    # per-artist webhooks (future). We thank the supporter on behalf of CP.
    _fire_supporter_shoutout(from_name, message, support_type, kofi_url, is_public)

    return HttpResponse('ok', status=200)


def _fire_supporter_shoutout(from_name, message, support_type, kofi_url, is_public):
    s = _settings()
    webhook = getattr(s, 'DISCORD_WEBHOOK_BOARD', '')
    cp_kofi = getattr(s, 'KOFI_PAGE', 'communityplaylist')
    cp_url  = f'https://ko-fi.com/{cp_kofi}'

    emoji = {'Subscription': '⭐', 'Shop Order': '🛒'}.get(support_type, '☕')
    label = {'Subscription': 'subscribed', 'Shop Order': 'placed a shop order'}.get(support_type, 'bought a coffee')

    # Discord embed
    if webhook:
        desc = f'**{from_name}** just {label} on Ko-fi! {emoji}'
        if message and is_public:
            desc += f'\n> {message}'
        desc += f'\n\n[Support Community Playlist]({cp_url})'
        embed = {
            'title':       f'{emoji} Ko-fi Support — Thank You!',
            'description': desc,
            'color':       0xFF5E5B,  # Ko-fi brand coral
            'url':         cp_url,
            'footer':      {'text': 'ko-fi.com/communityplaylist'},
        }
        _discord_post(webhook, {'embeds': [embed]})

    # Bluesky post
    token, did = _bsky_session()
    if token:
        pub_msg = f' "{message}"' if (message and is_public and len(message) < 120) else ''
        text = (
            f'{emoji} {from_name} just supported Community Playlist on Ko-fi!{pub_msg}\n\n'
            f'If you love free local PDX music listings, a coffee helps keep us running:\n'
            f'{cp_url}\n\n'
            f'#PDX #Portland #CommunityPlaylist'
        )[:300]
        facets = _bsky_facets(text, links=[cp_url], hashtags=['#PDX', '#Portland', '#CommunityPlaylist'])
        try:
            _bsky_create(token, did, text, facets=facets)
        except Exception as e:
            print(f'[Ko-fi] Bluesky shoutout error: {e}')


# ── Daily broadcast ───────────────────────────────────────────────────────────

def kofi_daily_broadcast(dry_run=False):
    """
    Post a daily Ko-fi awareness message to Discord + Bluesky.
    Designed to be called from the kofi_broadcast management command.
    Returns (discord_ok, bluesky_ok).
    """
    s = _settings()
    webhook  = getattr(s, 'DISCORD_WEBHOOK_KOFI', '') or getattr(s, 'DISCORD_WEBHOOK_BOARD', '')
    cp_kofi  = getattr(s, 'KOFI_PAGE', 'communityplaylist')
    cp_url   = f'https://ko-fi.com/{cp_kofi}'

    from django.utils import timezone
    day = timezone.now().strftime('%A')  # e.g. "Monday"

    discord_text = (
        f'☕ **Happy {day}, PDX!**\n\n'
        f'Community Playlist is free, ad-free, and community-run. '
        f'If you find value in local event listings, mixes, and artist pages — '
        f'a Ko-fi goes a long way.\n\n'
        f'**[Buy us a coffee ☕]({cp_url})**\n\n'
        f'Every supporter gets a shoutout here. Thank you! 🙏'
    )

    bsky_text = (
        f'☕ Happy {day}, PDX!\n\n'
        f'Community Playlist is free, ad-free, and run by locals. '
        f'If you enjoy free PDX event listings + artist mixes, a Ko-fi keeps the lights on.\n\n'
        f'{cp_url}\n\n'
        f'#PDX #Portland #PDXEvents #CommunityPlaylist'
    )[:300]

    discord_ok = False
    bluesky_ok = False

    if dry_run:
        print(f'[DRY] Discord:\n{discord_text}\n')
        print(f'[DRY] Bluesky:\n{bsky_text}\n')
        return True, True

    if webhook:
        embed = {
            'title':       '☕ Support Community Playlist on Ko-fi',
            'description': discord_text,
            'color':       0xFF5E5B,
            'url':         cp_url,
            'footer':      {'text': 'ko-fi.com/communityplaylist · free · ad-free · local'},
        }
        discord_ok = _discord_post(webhook, {'embeds': [embed]})

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
