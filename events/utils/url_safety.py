"""
URL safety utilities for CommunityPlaylist.

Rules:
  - Only https:// and http:// schemes are allowed (blocks javascript:, data:, etc.)
  - mailto: allowed explicitly for email links
  - Relative paths (/artists/slug/) pass through unchanged
  - Anything else is stripped to '' so templates render nothing rather than something dangerous
"""
from urllib.parse import urlparse

ALLOWED_SCHEMES = {'https', 'http', 'mailto'}


def is_safe_url(url: str) -> bool:
    """Return True if url is safe to render as an href."""
    if not url:
        return False
    url = url.strip()
    if url.startswith('/'):
        return True  # relative path — always safe
    try:
        parsed = urlparse(url)
        return parsed.scheme.lower() in ALLOWED_SCHEMES and bool(parsed.netloc)
    except Exception:
        return False


def sanitize_url(url: str, fallback: str = '') -> str:
    """Return url if safe, otherwise fallback (default empty string)."""
    if not url:
        return fallback
    url = url.strip()
    return url if is_safe_url(url) else fallback


def enforce_https(url: str) -> str:
    """Upgrade http:// to https://. Returns '' for unsafe URLs."""
    url = sanitize_url(url)
    if url.startswith('http://'):
        url = 'https://' + url[7:]
    return url


def display_domain(url: str) -> str:
    """Return just the domain for display, e.g. 'soundcloud.com'. Empty string if unsafe."""
    url = sanitize_url(url)
    if not url:
        return ''
    try:
        return urlparse(url).netloc.lstrip('www.')
    except Exception:
        return ''


# ── Discord notifications ─────────────────────────────────────────────────────

def discord_send(webhook_url: str, payload: dict) -> bool:
    """POST a JSON payload to a Discord webhook. Returns True on success."""
    if not webhook_url:
        return False
    try:
        import urllib.request as _ur
        import json as _json
        req = _ur.Request(
            webhook_url,
            data=_json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with _ur.urlopen(req, timeout=10):
            return True
    except Exception as e:
        print(f'[Discord] send failed: {e}')
        return False


# ── Feed health ───────────────────────────────────────────────────────────────

_HARD_FAIL_MARKERS = (
    '403 ', '404 ', 'SSLError', 'SSL:', 'certificate', 'Name or service not known'
)


def is_hard_feed_failure(error_str: str) -> bool:
    """True if error_str looks like a permanent feed failure (not a transient blip)."""
    if not error_str:
        return False
    return any(m in error_str for m in _HARD_FAIL_MARKERS)
