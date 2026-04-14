"""
Shared post content validation for board topics, replies, and neighborhood board posts.

Rules:
  1. Hard-blocked keywords (CSAM, dangerous content) — always rejected, no account bypass
  2. URLs in posts — only allowed for verified accounts (email_verified=True)
  3. More than 2 URLs — always rejected
  4. Shortened/obfuscated URLs (bit.ly, lmy.de, t.me, etc.) — always rejected
"""
import re


# ── Hard-block patterns ───────────────────────────────────────────────────────
# CSAM, CSAM-adjacent, and commonly-abused spam phrases. Always rejected.
_HARDBLOCK_RE = re.compile(
    r'OPVA'
    r'|РТНС|ртнс'                         # Cyrillic disguise of PTHC
    r'|\bpthc\b|\bptsc\b|\bpedo\b'
    r'|\blolita\b|\bjailbait\b'
    r'|\bpreteen\b|\bpre.?teen\b'
    r'|\bchild.?porn|\bcp.?free\b|\bdownload.?all.?cp\b'
    r'|\bsibirian.?mouse\b|\bbibigon\b|\bfalkovideo\b'
    r'|\bstickam\b|\bvichatter\b'
    r'|\bParadise.?Birds\b|\bGoldbergVideo\b|\bFantasia.?Models\b'
    r'|\bno.?pay.?premium\b'
    r'|\bultimate.*(collection|archive)\b',
    re.I,
)

# ── URL patterns ──────────────────────────────────────────────────────────────
_URL_RE = re.compile(r'https?://\S+|www\.\S{4,}', re.I)

# Shortened / obfuscated URLs: word.short-tld/path (e.g. lmy.de/abc, bit.ly/xyz)
_SHORT_URL_RE = re.compile(
    r'\b\w{2,20}\.(de|ru|ly|me|to|cc|pw|tk|ml|ga|cf|gq|io|su|xyz|top|club)'
    r'/\S{3,}',
    re.I,
)


def _count_urls(text):
    return len(_URL_RE.findall(text)) + len(_SHORT_URL_RE.findall(text))


def check_post(title='', body='', user=None):
    """
    Returns (ok: bool, error_message: str|None).
    Pass request.user as `user`; AnonymousUser or None means not logged in.
    """
    text = f"{title} {body}"

    # 1. Hard block — always, no bypass
    if _HARDBLOCK_RE.search(text):
        return False, 'This post was blocked by the content filter.'

    # 2. Shortened / obfuscated URLs — always blocked
    if _SHORT_URL_RE.search(text):
        return False, 'Shortened or obfuscated URLs are not allowed.'

    # 3. Any URLs present?
    if _URL_RE.search(text):
        # Too many links
        if _count_urls(text) > 2:
            return False, 'Posts with more than 2 links are not allowed.'
        # Must be a verified account
        is_authed = user is not None and getattr(user, 'is_authenticated', False)
        if not is_authed:
            return False, 'Create a verified account to include links in posts.'
        try:
            from events.models import UserProfile
            profile = UserProfile.objects.get(user=user)
            if not profile.email_verified:
                return False, 'Verify your email address before including links in posts.'
        except Exception:
            return False, 'Verify your email address before including links in posts.'

    return True, None
