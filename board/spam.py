"""
Shared post content validation for board topics, replies, and offerings.

Rules (in order):
  1. Hard-blocked keywords (CSAM, dangerous content) — always rejected
  2. Spam/gambling/scam keyword block — always rejected
  3. Shortened / obfuscated URLs — always rejected
  4. Bare domain links (no https://) — same TLD blocklist applied
  5. URLs — only allowed for verified accounts with email confirmed
  6. More than 2 URLs — always rejected
  7. Timing honeypot — submissions faster than MIN_SECONDS are bots
  8. New account cooldown — accounts < ACCOUNT_AGE_DAYS old treated as guests
  9. Bot-name pattern — Name+digits (e.g. Orren8v) flagged for guest posts
"""
import re
from datetime import timedelta
from django.utils import timezone


# ── Hard-block patterns ───────────────────────────────────────────────────────
_HARDBLOCK_RE = re.compile(
    r'OPVA'
    r'|РТНС|ртнс'
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

# ── Spam / scam / gambling keywords ──────────────────────────────────────────
_SPAM_RE = re.compile(
    r'\b(jackpot|instant.?jackpot|claim.?your|start.?winning|cash.?out'
    r'|deposit.?win|slot.?machine|online.?casino|crypto.?bonus'
    r'|forex.?signal|binary.?option|make.?money.?fast|earn.?\$\d+'
    r'|work.?from.?home.?earn|payday.?loan|no.?credit.?check'
    r'|click.?here.?to.?win|free.?gift.?card|gift.?card.?giveaway'
    r'|weight.?loss.?pill|diet.?pill|male.?enhancement'
    r'|replica.?watch|replica.?bag|cheap.?ray.?ban'
    r'|seo.?service|buy.?(followers|likes|views|subscribers)'
    r'|increase.?your.?rank|guaranteed.?traffic'
    r'|escort|call.?girl|adult.?dating|hookup.?site)\b',
    re.I,
)

# ── URL patterns ──────────────────────────────────────────────────────────────
_URL_RE = re.compile(r'https?://\S+|www\.\S{4,}', re.I)

# Shortened / obfuscated URLs — with or without protocol
# Covers word.tld/path (e.g. pse.is/abc, bit.ly/xyz, lmy.de/foo)
_SHORT_TLD = (
    r'de|ru|ly|me|to|cc|pw|tk|ml|ga|cf|gq|su'
    r'|xyz|top|club|is|co|vn|link|click|site|online|tech|live|fun|icu'
)
_SHORT_URL_RE = re.compile(
    r'(?:https?://)?\b\w{2,20}\.(' + _SHORT_TLD + r')/\S{2,}',
    re.I,
)

# Bare domain pattern — catches "pse.is/abc" even without slashes in some forms
_BARE_DOMAIN_RE = re.compile(
    r'\b[a-z0-9-]{2,20}\.(' + _SHORT_TLD + r')\b',
    re.I,
)

# Minimum seconds between page load and submit — bots are faster
_MIN_SECONDS = 4
# Account age in days before treated as trusted (can post links)
_ACCOUNT_AGE_DAYS = 3
# Bot-name pattern: letters followed immediately by digits (Orren8v, User123)
_BOT_NAME_RE = re.compile(r'^[a-z]{3,}[0-9]{1,4}[a-z]?$', re.I)


def _count_urls(text):
    return len(_URL_RE.findall(text)) + len(_SHORT_URL_RE.findall(text))


def _is_new_account(user):
    """True if the account is younger than _ACCOUNT_AGE_DAYS days."""
    try:
        cutoff = timezone.now() - timedelta(days=_ACCOUNT_AGE_DAYS)
        return user.date_joined > cutoff
    except Exception:
        return False


def check_timing(form_time_str):
    """
    Returns (ok, error). Call with the value of the hidden _t field.
    Fails open if the field is missing or unparseable — never blocks legit posts
    on JS failure.
    """
    if not form_time_str:
        return True, None  # JS didn't fire — don't block
    try:
        ts = float(form_time_str)
        elapsed = timezone.now().timestamp() - ts
        if elapsed < _MIN_SECONDS:
            return False, 'Form submitted too quickly. Please try again.'
        if elapsed > 86400:
            return False, 'Session expired. Refresh the page and try again.'
    except (ValueError, TypeError):
        pass  # malformed value — fail open
    return True, None


def check_post(title='', body='', author_name='', user=None):
    """
    Returns (ok: bool, error_message: str|None).
    Pass request.user as `user`; AnonymousUser or None means not logged in.
    """
    text = f"{title} {body}"

    # 1. Hard block
    if _HARDBLOCK_RE.search(text):
        return False, 'This post was blocked by the content filter.'

    # 2. Spam / scam / gambling keywords
    if _SPAM_RE.search(text):
        return False, 'This post was blocked by the content filter.'

    # 3. Shortened / obfuscated URLs — always blocked
    if _SHORT_URL_RE.search(text):
        return False, 'Shortened or obfuscated URLs are not allowed.'

    # 4. Bare suspicious domains (no slash needed — catches pse.is alone)
    if _BARE_DOMAIN_RE.search(text):
        return False, 'Links to external short domains are not allowed.'

    # 5–6. Any URLs present?
    if _URL_RE.search(text):
        if _count_urls(text) > 2:
            return False, 'Posts with more than 2 links are not allowed.'
        is_authed = user is not None and getattr(user, 'is_authenticated', False)
        if not is_authed:
            return False, 'Create a verified account to include links in posts.'
        if _is_new_account(user):
            return False, 'New accounts must wait a few days before posting links.'
        try:
            from events.models import UserProfile
            profile = UserProfile.objects.get(user=user)
            if not profile.email_verified:
                return False, 'Verify your email address before including links in posts.'
        except Exception:
            return False, 'Verify your email address before including links in posts.'

    # 9. Bot-name pattern for guest posts
    is_authed = user is not None and getattr(user, 'is_authenticated', False)
    if not is_authed and author_name and _BOT_NAME_RE.match(author_name.strip()):
        return False, 'That name looks like it might be automated. Please use your real name or handle.'

    return True, None
