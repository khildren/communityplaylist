"""
Duplicate event cleanup.
Pass --dry-run to preview without deleting.
"""
import django, os, sys, re
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "communityplaylist.settings")
django.setup()

from django.utils.timezone import localtime
from events.models import Event

DRY_RUN = '--dry-run' in sys.argv


def normalize(title):
    """Lowercase, strip punctuation/articles, return sorted token set."""
    t = title.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    stopwords = {"the", "a", "an", "and", "with", "presents", "featuring", "ft", "vs", "at", "in"}
    tokens = {w for w in t.split() if w and w not in stopwords}
    return tokens


def title_overlap(a, b):
    """Fraction of shorter title's tokens found in the longer title."""
    ta, tb = normalize(a), normalize(b)
    if not ta or not tb:
        return 0.0
    shorter = ta if len(ta) <= len(tb) else tb
    longer  = ta if len(ta) >  len(tb) else tb
    return len(shorter & longer) / len(shorter)


def score(event):
    """Higher = better record to keep. Prefer more info, earlier id."""
    return (
        len(event.description or ""),
        len(event.title),
        bool(event.photo),
        -event.id,   # lower id = submitted first = preferred
    )


# ── Exact dedup (title + start_date) ────────────────────────────────────────
exact_seen = {}
exact_deleted = []

for e in Event.objects.order_by("title", "start_date", "id"):
    key = (e.title.strip().lower(), str(e.start_date))
    if key in exact_seen:
        exact_deleted.append(e)
    else:
        exact_seen[key] = e

# ── Fuzzy dedup (same location + same day, overlapping titles) ──────────────
# Group by (location, date)
groups = defaultdict(list)
all_events = Event.objects.order_by("id")
deleted_ids = {e.id for e in exact_deleted}

for e in all_events:
    if e.id in deleted_ids:
        continue
    day = localtime(e.start_date).date()
    loc = (e.location or "").strip().lower()[:60]
    if loc:
        groups[(loc, day)].append(e)

fuzzy_deleted = []
OVERLAP_THRESHOLD = 0.70  # 70% of shorter title's tokens must match

for (loc, day), group in groups.items():
    if len(group) < 2:
        continue
    # Compare every pair in the group
    keep = set(range(len(group)))
    for i in range(len(group)):
        for j in range(i + 1, len(group)):
            if i not in keep or j not in keep:
                continue
            overlap = title_overlap(group[i].title, group[j].title)
            if overlap >= OVERLAP_THRESHOLD:
                # Drop the lower-scoring one
                drop = i if score(group[i]) < score(group[j]) else j
                keep.discard(drop)
                dropped_event = group[drop]
                if dropped_event.id not in deleted_ids:
                    fuzzy_deleted.append(dropped_event)
                    deleted_ids.add(dropped_event.id)

# ── Report & execute ─────────────────────────────────────────────────────────
all_to_delete = exact_deleted + fuzzy_deleted
print(f"Exact duplicates:  {len(exact_deleted)}")
print(f"Fuzzy duplicates:  {len(fuzzy_deleted)}")
print(f"Total to delete:   {len(all_to_delete)}")

if fuzzy_deleted:
    print("\nFuzzy matches found:")
    for e in fuzzy_deleted:
        print(f"  [{e.id}] {e.title[:60]} @ {localtime(e.start_date).strftime('%b %d %H:%M')} — {e.location[:40]}")

if DRY_RUN:
    print("\n[DRY RUN] Nothing deleted.")
else:
    ids = [e.id for e in all_to_delete]
    if ids:
        Event.objects.filter(id__in=ids).delete()
    print(f"\nDeleted {len(ids)}. Remaining: {Event.objects.count()}")
