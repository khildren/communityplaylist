"""
profile_completeness — Score and report thin Artist, Promoter, and Venue profiles.

Calculates a simple weighted completeness score (0–100) for each profile
and lists the lowest-scoring ones so you know where to focus editorial effort.

Usage:
  python manage.py profile_completeness
  python manage.py profile_completeness --top 20     # show worst 20 per type
  python manage.py profile_completeness --min-score 60  # only show below 60%
  python manage.py profile_completeness --model artist
"""
from django.core.management.base import BaseCommand

from events.models import Artist, PromoterProfile, Venue


def _score_artist(a) -> tuple[int, list[str]]:
    """Returns (score 0-100, list of missing fields)."""
    points = missing = 0

    def check(val, pts, label):
        nonlocal points, missing
        if val:
            points += pts
        else:
            missing += pts
            return label
        return None

    gaps = []
    for result in [
        check(a.bio,   20, 'bio'),
        check(a.photo, 15, 'photo'),
        check(a.website or a.instagram or a.soundcloud or a.bandcamp
              or a.mixcloud or a.youtube or a.spotify, 15, 'social/web link'),
        check(a.city,  10, 'city'),
        check(a.drive_folder_url or a.house_mixes or a.youtube, 10, 'audio/video'),
        check(a.claimed_by_id, 10, 'claimed'),
        check(a.instagram, 10, 'instagram'),
        check(a.soundcloud or a.bandcamp or a.mixcloud or a.spotify, 10, 'music platform'),
    ]:
        if result:
            gaps.append(result)

    total = points + missing
    score = round(points * 100 / total) if total else 0
    return score, gaps


def _score_promoter(p) -> tuple[int, list[str]]:
    points = missing = 0

    def check(val, pts, label):
        nonlocal points, missing
        if val:
            points += pts
        else:
            missing += pts
            return label
        return None

    gaps = []
    for result in [
        check(p.bio,    20, 'bio'),
        check(p.photo,  15, 'photo'),
        check(p.website or p.instagram or p.soundcloud, 15, 'social/web link'),
        check(p.instagram, 10, 'instagram'),
        check(p.genres.exists(), 10, 'genres'),
        check(p.members.exists(), 10, 'members/artists'),
        check(p.claimed_by_id, 10, 'claimed'),
        check(p.drive_folder_url, 10, 'audio folder'),
    ]:
        if result:
            gaps.append(result)

    total = points + missing
    score = round(points * 100 / total) if total else 0
    return score, gaps


def _score_venue(v) -> tuple[int, list[str]]:
    points = missing = 0

    def check(val, pts, label):
        nonlocal points, missing
        if val:
            points += pts
        else:
            missing += pts
            return label
        return None

    gaps = []
    for result in [
        check(v.description, 20, 'description'),
        check(v.logo,        15, 'logo/photo'),
        check(v.neighborhood, 15, 'neighborhood'),
        check(v.website,     15, 'website'),
        check(v.latitude and v.longitude, 15, 'geocoords'),
        check(v.instagram or v.website,   10, 'social/web'),
        check(v.claimed_by_id,            10, 'claimed'),
    ]:
        if result:
            gaps.append(result)

    total = points + missing
    score = round(points * 100 / total) if total else 0
    return score, gaps


class Command(BaseCommand):
    help = 'Report completeness scores for Artist, Promoter, and Venue profiles'

    def add_arguments(self, parser):
        parser.add_argument('--top', type=int, default=15,
                            help='Show this many lowest-scoring profiles per type (default 15)')
        parser.add_argument('--min-score', type=int, default=80,
                            help='Only show profiles scoring below this value (default 80)')
        parser.add_argument('--model', choices=['artist', 'promoter', 'venue', 'all'],
                            default='all', help='Which model type to check (default all)')

    def _bar(self, score):
        filled = round(score / 10)
        return '█' * filled + '░' * (10 - filled)

    def _section(self, label, rows, top, min_score):
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING(f'{label}'))

        low = [(s, g, n) for s, g, n in rows if s < min_score]
        low.sort(key=lambda x: x[0])
        shown = low[:top]

        if not shown:
            self.stdout.write(self.style.SUCCESS(f'  All {len(rows)} profiles score ≥ {min_score}%.'))
            return

        for score, gaps, name in shown:
            bar = self._bar(score)
            gap_str = ', '.join(gaps[:4]) + (f' +{len(gaps)-4} more' if len(gaps) > 4 else '')
            colour = self.style.ERROR if score < 40 else (
                self.style.WARNING if score < 70 else self.style.SUCCESS)
            self.stdout.write(
                colour(f'  {score:3d}%  {bar}  {name[:50]:<50}  missing: {gap_str}')
            )

        if len(low) > top:
            self.stdout.write(f'  … and {len(low) - top} more below {min_score}%')

        avg = round(sum(s for s, _, _ in rows) / len(rows)) if rows else 0
        self.stdout.write(
            f'  Total: {len(rows)}  |  Below {min_score}%: {len(low)}  |  Avg score: {avg}%'
        )

    def handle(self, *args, **options):
        top       = options['top']
        min_score = options['min_score']
        model     = options['model']

        if model in ('artist', 'all'):
            artists = list(
                Artist.objects.filter(is_stub=False)
                .order_by('name')
            )
            rows = [(s, g, a.name) for a in artists for s, g in [_score_artist(a)]]
            self._section('Artist Profiles', rows, top, min_score)

        if model in ('promoter', 'all'):
            promoters = list(
                PromoterProfile.objects.filter(is_public=True)
                .prefetch_related('genres', 'members')
                .order_by('name')
            )
            rows = [(s, g, p.name) for p in promoters for s, g in [_score_promoter(p)]]
            self._section('Promoter Profiles', rows, top, min_score)

        if model in ('venue', 'all'):
            venues = list(
                Venue.objects.filter(active=True)
                .order_by('name')
            )
            rows = [(s, g, v.name) for v in venues for s, g in [_score_venue(v)]]
            self._section('Venue Profiles', rows, top, min_score)
