"""
flyer_scan — scan an event flyer image using local Ollama (moondream).

Runs entirely on-box (tokyo7 Unraid at 10.0.0.124:11434) — zero API cost.

Usage:
    from events.utils.flyer_scan import scan_flyer
    result = scan_flyer("https://instagram.com/p/ABC123/")
    result = scan_flyer("https://cdn.example.com/flyer.jpg")
    # result = {"title": ..., "date": ..., "artists": [...], ...}
"""
import base64
import json
import re
import urllib.request
import urllib.error

from django.conf import settings

OLLAMA_URL   = getattr(settings, 'OLLAMA_URL', 'http://10.0.0.124:11434')
FLYER_MODEL  = getattr(settings, 'OLLAMA_FLYER_MODEL', 'moondream')
_TIMEOUT     = 90  # moondream is fast but give it room

_FETCH_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; CommunityPlaylist/1.0)',
    'Accept': 'text/html,application/xhtml+xml,image/*,*/*',
}

_PROMPT = (
    "This is an event flyer. Read every word carefully. "
    "Respond ONLY with a JSON object — no explanation, no markdown. "
    "Keys: title (string), date (YYYY-MM-DD or null), doors_time (HH:MM 24h or null), "
    "start_time (HH:MM 24h or null), venue_name (string or null), "
    "venue_address (string or null), artists (array of strings), "
    "price (string e.g. '$15' or 'free' or null), ticket_url (URL string or null), "
    "genre (string or null), extra_text (any other notable text or null). "
    "If a field is not visible use null. Artists must be an array."
)


def _resolve_image_url(source_url: str) -> str | None:
    """
    Given an Instagram post URL or a direct image URL, return a fetchable image URL.
    For Instagram posts we pull og:image from the page HTML.
    """
    if re.search(r'\.(jpe?g|png|gif|webp|avif)(\?|$)', source_url, re.I):
        return source_url

    if 'instagram.com/p/' in source_url or 'instagram.com/reel/' in source_url:
        try:
            req = urllib.request.Request(source_url, headers=_FETCH_HEADERS)
            with urllib.request.urlopen(req, timeout=15) as r:
                html = r.read(65536).decode('utf-8', errors='ignore')
            m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](https?://[^"\']+)["\']', html)
            if not m:
                m = re.search(r'content=["\'](https?://[^"\']+)["\'][^>]+property=["\']og:image["\']', html)
            if m:
                return m.group(1)
        except Exception:
            pass
        return None

    # Generic page — try og:image
    try:
        req = urllib.request.Request(source_url, headers=_FETCH_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read(65536).decode('utf-8', errors='ignore')
        m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](https?://[^"\']+)["\']', html)
        if not m:
            m = re.search(r'content=["\'](https?://[^"\']+)["\'][^>]+property=["\']og:image["\']', html)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _download_b64(image_url: str) -> str | None:
    """Download image and return base64-encoded bytes, or None on failure."""
    try:
        req = urllib.request.Request(image_url, headers=_FETCH_HEADERS)
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read(10 * 1024 * 1024)  # cap at 10 MB
        return base64.b64encode(raw).decode('ascii')
    except Exception:
        return None


def _call_ollama(b64_image: str) -> str:
    """POST to Ollama generate endpoint, return raw response text."""
    payload = json.dumps({
        'model':  FLYER_MODEL,
        'prompt': _PROMPT,
        'images': [b64_image],
        'stream': False,
    }).encode()
    req = urllib.request.Request(
        f'{OLLAMA_URL}/api/generate',
        data=payload,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        resp = json.loads(r.read())
    return resp.get('response', '')


def _parse_response(raw: str) -> dict:
    """Extract JSON from Ollama's response text. Returns {} on parse failure."""
    raw = raw.strip()
    # Strip markdown code fences if model adds them
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try to find the first {...} block
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                return {}
        else:
            return {}

    # Normalise artists to list
    if isinstance(data.get('artists'), str):
        data['artists'] = [a.strip() for a in re.split(r'[,/\n]+', data['artists']) if a.strip()]
    elif not isinstance(data.get('artists'), list):
        data['artists'] = []

    # Strip nulls
    return {k: v for k, v in data.items() if v is not None and v != ''}


def scan_flyer(source_url: str) -> dict:
    """
    Main entry point. Accepts an Instagram post URL, a direct image URL,
    or any page with an og:image. Returns a dict of extracted event fields.
    Returns {} if scanning fails for any reason.
    """
    image_url = _resolve_image_url(source_url)
    if not image_url:
        return {}

    b64 = _download_b64(image_url)
    if not b64:
        return {}

    try:
        raw = _call_ollama(b64)
    except Exception:
        return {}

    return _parse_response(raw)
