import requests
import time
from django.core.management.base import BaseCommand
from events.models import Genre


class Command(BaseCommand):
    help = "Import genres from MusicBrainz slowly"

    def handle(self, *a, **k):
        offset = Genre.objects.count()
        self.stdout.write(f"Starting from offset {offset}")
        total = 9999
        imported = 0

        while offset < total:
            try:
                r = requests.get(
                    f"https://musicbrainz.org/ws/2/genre/all?limit=25&offset={offset}&fmt=json",
                    headers={"User-Agent": "CommunityPlaylist/1.0 (andrew.jubinsky@proton.me)"}
                )
                data = r.json()
                total = data["genre-count"]

                for g in data["genres"]:
                    Genre.objects.get_or_create(
                        name=g["name"],
                        defaults={"mb_id": g["id"]}
                    )
                    imported += 1

                offset += 25
                self.stdout.write(f"{Genre.objects.count()}/{total} genres...")
                time.sleep(1)

            except Exception as e:
                self.stdout.write(f"Error at offset {offset}: {e}, retrying in 10s...")
                time.sleep(10)

        self.stdout.write(f"Done - {Genre.objects.count()} genres total")