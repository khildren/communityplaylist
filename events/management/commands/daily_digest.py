import requests, time
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.timezone import localtime
from events.models import Event
WEBHOOK="https://discord.com/api/webhooks/1487258605102039051/aMDBINHJSRTE2DVRB7AIdEQpC-5pacJgEKwEn9_gf6nhJbCLlsXD41zADDIlP-5Md5CC"
LOGO="https://hihi.communityplaylist.com/files/timeline_files/store_file6809b5ed4135d-community_playlist_site_logo_2025.png"
class Command(BaseCommand):
    help="Post todays events to Discord"
    def handle(self,*a,**k):
        now=timezone.now()
        today_start=now.replace(hour=0,minute=0,second=0,microsecond=0)
        today_end=now.replace(hour=23,minute=59,second=59,microsecond=0)
        events=Event.objects.filter(status="approved",start_date__gte=today_start,start_date__lte=today_end).order_by("start_date")
        if not events.exists():
            requests.post(WEBHOOK,json={"content":"📅 No events today — check upcoming at https://communityplaylist.com"})
            return
        requests.post(WEBHOOK,json={"content":f"🌹 **Todays PDX Events — {localtime(now).strftime(\"%A, %B %d\")}**\nhttps://communityplaylist.com"})
        time.sleep(1)
        for e in events:
            genres=", ".join(e.genres.values_list("name",flat=True)) or "various"
            img=f"https://communityplaylist.com{e.photo.url}" if e.photo else LOGO
            payload={"embeds":[{"title":e.title,"url":f"https://communityplaylist.com/events/{e.slug}/","color":0xff6b35,"fields":[{"name":"📅 Time","value":localtime(e.start_date).strftime("%I:%M %p"),"inline":True},{"name":"📍 Location","value":e.location[:100],"inline":True},{"name":"🎵 Genre","value":genres,"inline":True},{"name":"💰 Cost","value":"FREE" if e.is_free else e.price_info or "Paid","inline":True}],"thumbnail":{"url":img}}]}
            requests.post(WEBHOOK,json=payload)
            time.sleep(1)
        self.stdout.write(f"Posted {events.count()} events")
