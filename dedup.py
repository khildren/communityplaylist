import django, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE","communityplaylist.settings")
django.setup()
from events.models import Event
seen = {}
deleted = 0
for e in Event.objects.order_by("title","start_date","id"):
    key = (e.title, str(e.start_date))
    if key in seen:
        e.delete()
        deleted += 1
    else:
        seen[key] = e.id
print(f"Deleted {deleted} duplicates. Remaining: {Event.objects.count()}")
