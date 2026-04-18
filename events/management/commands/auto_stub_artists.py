"""
auto_stub_artists — scan approved events for artist names appearing in ≥3 shows
without an existing Artist profile, and create a minimal stub for each.

Run:
    python manage.py auto_stub_artists
    python manage.py auto_stub_artists --min-shows 5 --dry-run
"""
from django.core.management.base import BaseCommand
from django.db.models import Count


class Command(BaseCommand):
    help = 'Auto-create stub Artist profiles for names appearing in ≥N approved events.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--min-shows', type=int, default=3,
            help='Minimum number of approved events an artist must appear in (default: 3)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print what would be created without writing to the database.',
        )

    def handle(self, *args, **options):
        from events.models import Artist, Event

        min_shows = options['min_shows']
        dry_run   = options['dry_run']

        # Collect all artist names linked to approved events, count occurrences
        qs = (
            Event.objects
            .filter(status='approved')
            .values('artists__name', 'artists__id')
            .annotate(show_count=Count('id', distinct=True))
            .filter(show_count__gte=min_shows)
            .exclude(artists__isnull=True)
            .order_by('-show_count')
        )

        created = 0
        skipped = 0

        for row in qs:
            artist_id   = row['artists__id']
            artist_name = row['artists__name']
            show_count  = row['show_count']

            if not artist_id or not artist_name:
                continue

            try:
                artist = Artist.objects.get(pk=artist_id)
            except Artist.DoesNotExist:
                continue

            # Already has a meaningful profile — skip
            has_profile = any([
                artist.bio,
                artist.website,
                artist.instagram,
                artist.soundcloud,
                artist.bandcamp,
                artist.photo,
                artist.claimed_by_id,
            ])
            if has_profile:
                skipped += 1
                continue

            if dry_run:
                self.stdout.write(
                    f'[DRY RUN] Would mark as stub: "{artist_name}" ({show_count} shows, id={artist_id})'
                )
            else:
                # Ensure slug is set (may be blank on very old stubs)
                if not artist.slug:
                    artist.save()   # triggers slug generation in Artist.save()
                    self.stdout.write(f'  Slug generated for {artist_name}')

                self.stdout.write(
                    self.style.SUCCESS(
                        f'Stub confirmed: "{artist_name}" ({show_count} shows) → /artists/{artist.slug}/'
                    )
                )

            created += 1

        self.stdout.write(
            f'\nDone. {created} stubs {"would be " if dry_run else ""}processed, {skipped} already had profile data.'
        )
