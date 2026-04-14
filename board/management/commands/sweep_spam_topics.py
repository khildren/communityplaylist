"""
management command: python manage.py sweep_spam_topics

Three-tier sweep of board topics:

  TIER 0 — Hard-block (CSAM / dangerous content) → INSTANT delete, even with replies
    - CSAM keywords, solicitation phrases, known abuse site names
    - Shortened / obfuscated URLs (bit.ly, lmy.de, t.me, etc.)

  TIER 1 — Obvious spam (no replies) → deleted after 24 hours
    - Non-Latin / non-ASCII script in title (Devanagari, Cyrillic, CJK, Arabic, etc.)
    - Title is gibberish (very short, no real words, random char sequences)
    - Body or title contains known spam keywords (casino, betting, IPL, earn money…)
    - Title/body contains a bare URL

  TIER 2 — Looks like a real post, just unanswered → deleted after 14 days
    - Normal English title and body
    - Gives the community two weeks to reply before it's cleaned up

  ALWAYS KEPT (except tier 0)
    - Any topic with at least one reply
    - Recurring event threads (linked to a RecurringEvent)
    - Posts by 'Community Playlist' (auto-generated threads)

Run via cron twice daily:
  0 4,16 * * *  cd /path && venv/bin/python manage.py sweep_spam_topics
"""
import re
from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone
from datetime import timedelta
from board.models import Topic


# ── Hard-block: CSAM and dangerous content ─────────────────────────────────
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

# Shortened / obfuscated URLs
_SHORT_URL_RE = re.compile(
    r'\b\w{2,20}\.(de|ru|ly|me|to|cc|pw|tk|ml|ga|cf|gq|io|su|xyz|top|club)'
    r'/\S{3,}',
    re.I,
)

# ── Spam detection ─────────────────────────────────────────────────────────

# Non-Latin Unicode blocks: Devanagari, Arabic, CJK, Cyrillic, Hebrew, Thai…
_NON_LATIN_RE = re.compile(
    r'[\u0900-\u097F'   # Devanagari
    r'\u0600-\u06FF'    # Arabic
    r'\u4E00-\u9FFF'    # CJK Unified
    r'\u3040-\u30FF'    # Hiragana / Katakana
    r'\uAC00-\uD7AF'    # Hangul
    r'\u0400-\u04FF'    # Cyrillic
    r'\u0590-\u05FF'    # Hebrew
    r'\u0E00-\u0E7F'    # Thai
    r']'
)

_URL_RE = re.compile(r'https?://|www\.\S+\.\S+')

_SPAM_KEYWORDS = re.compile(
    r'\b(casino|bet(ting)?|ipl|cricket|earn money|make money|prize|forex|crypto invest'
    r'|poker|slot|gambling|click here|whatsapp|telegram|@gmail|@yahoo)\b',
    re.I,
)

# Gibberish: title has no vowels after stripping, or is all lowercase random chars
_GIBBERISH_RE = re.compile(r'^[a-z]{8,}$')  # long all-lowercase blob with no spaces


def _is_hardblock(topic):
    text = f"{topic.title} {topic.body}"
    return bool(_HARDBLOCK_RE.search(text) or _SHORT_URL_RE.search(text))


def _is_obvious_spam(topic):
    text = f"{topic.title} {topic.body}"
    if _NON_LATIN_RE.search(text):
        return True
    if _URL_RE.search(text):
        return True
    if _SPAM_KEYWORDS.search(text):
        return True
    title = topic.title.strip()
    # Gibberish: single word, 8+ chars, all lowercase, no spaces
    if _GIBBERISH_RE.match(title) and ' ' not in title:
        return True
    # Very short body (under 20 chars) AND short title (under 10 chars)
    if len(topic.body.strip()) < 20 and len(title) < 10:
        return True
    return False


class Command(BaseCommand):
    help = 'Three-tier spam sweep: instant for CSAM/hard-block, 24h for spam, 14-day grace for genuine posts'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Preview what would be deleted without deleting anything'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        now = timezone.now()
        spam_cutoff  = now - timedelta(hours=24)   # tier 1: 24 h old
        grace_cutoff = now - timedelta(days=14)    # tier 2: 14 days old

        # Tier 0: hard-block — check ALL topics, including those with replies
        all_topics = (
            Topic.objects
            .exclude(author_name='Community Playlist')
        )

        tier0 = [t for t in all_topics if _is_hardblock(t)]

        # Tiers 1 & 2: no replies, not recurring, not system-posted
        base = (
            Topic.objects
            .annotate(rc=Count('replies'))
            .filter(rc=0, recurring_event__isnull=True)
            .exclude(author_name='Community Playlist')
            .exclude(pk__in=[t.pk for t in tier0])
        )

        tier1 = []  # obvious spam ≥ 24 h old
        tier2 = []  # genuine-looking ≥ 14 days old

        for topic in base.order_by('created_at'):
            if _is_obvious_spam(topic):
                if topic.created_at <= spam_cutoff:
                    tier1.append(topic)
            else:
                if topic.created_at <= grace_cutoff:
                    tier2.append(topic)

        total = len(tier0) + len(tier1) + len(tier2)

        if total == 0:
            self.stdout.write('Nothing to sweep.')
            return

        if tier0:
            self.stdout.write(self.style.ERROR(f'\nTier 0 — HARD BLOCK / CSAM ({len(tier0)}):'))
            for t in tier0:
                self.stdout.write(f'  [{t.created_at.date()}] {t.author_name!r}: {t.title[:80]}')

        if tier1:
            self.stdout.write(f'\nTier 1 — obvious spam ({len(tier1)}):')
            for t in tier1:
                self.stdout.write(f'  [{t.created_at.date()}] {t.author_name!r}: {t.title[:80]}')

        if tier2:
            self.stdout.write(f'\nTier 2 — unanswered ≥14 days ({len(tier2)}):')
            for t in tier2:
                self.stdout.write(f'  [{t.created_at.date()}] {t.author_name!r}: {t.title[:80]}')

        if dry_run:
            self.stdout.write(f'\n[dry-run] Would delete {total} topic(s). Nothing changed.')
            return

        ids = [t.pk for t in tier0 + tier1 + tier2]
        Topic.objects.filter(pk__in=ids).delete()
        self.stdout.write(self.style.SUCCESS(
            f'\nSwept {len(tier0)} hard-block + {len(tier1)} spam + {len(tier2)} stale = {total} total.'
        ))
