"""
check_media_files — Detect orphaned photo/logo references and malformed Drive URLs.

Checks:
  1. Artist, PromoterProfile, Venue, and RecurringEvent photo/logo fields that
     point to a path that does not exist on disk (dangling ImageField reference).
  2. Artist and PromoterProfile drive_folder_url values that are missing or
     look malformed (not a valid Google Drive URL pattern).
  3. PlaylistTrack stream_url entries that are blank but have a drive_file_id
     (un-cached tracks that will silently fail in the player).

Usage:
  python manage.py check_media_files
  python manage.py check_media_files --fix-dangling   # clear broken photo fields
  python manage.py check_media_files --dry-run
"""
import os
import re

from django.conf import settings
from django.core.management.base import BaseCommand

from events.models import Artist, PlaylistTrack, PromoterProfile, RecurringEvent, Venue

DRIVE_FOLDER_RE = re.compile(
    r'https://drive\.google\.com/(drive/folders/|open\?id=|folderview\?id=)'
)


def _check_photo(obj, field_name, media_root):
    """Returns the broken path string, or None if OK."""
    field = getattr(obj, field_name)
    if not field:
        return None
    full = os.path.join(media_root, str(field))
    return full if not os.path.exists(full) else None


class Command(BaseCommand):
    help = 'Report orphaned photo/logo files and malformed Drive folder URLs'

    def add_arguments(self, parser):
        parser.add_argument('--fix-dangling', action='store_true',
                            help='Clear photo/logo fields whose files are missing from disk')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report only — never write to the database')

    def handle(self, *args, **options):
        fix     = options['fix_dangling'] and not options['dry_run']
        dry_run = options['dry_run']
        media_root = str(settings.MEDIA_ROOT)

        dangling_count = drive_bad_count = uncached_count = 0

        # ── 1. Dangling photo/logo files ──────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING('Dangling photo/logo references'))

        checks = [
            ('Artist',    Artist.objects.filter(photo__gt='').order_by('name'),    'photo'),
            ('Promoter',  PromoterProfile.objects.filter(photo__gt='').order_by('name'), 'photo'),
            ('Venue',     Venue.objects.filter(logo__gt='').order_by('name'),      'logo'),
            ('Recurring', RecurringEvent.objects.filter(photo__gt='').order_by('title'), 'photo'),
        ]

        for label, qs, field in checks:
            for obj in qs:
                broken_path = _check_photo(obj, field, media_root)
                if broken_path:
                    dangling_count += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f'  MISSING  {label}: {str(obj)[:50]}  →  {broken_path}'
                        )
                    )
                    if fix:
                        obj.__class__.objects.filter(pk=obj.pk).update(**{field: ''})
                        self.stdout.write(f'    ↳ cleared {field} field')

        if not dangling_count:
            self.stdout.write(self.style.SUCCESS('  All photo/logo files present on disk.'))

        # ── 2. Malformed / missing drive_folder_url ────────────────────────
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Drive folder URL health'))

        drive_sources = [
            ('Artist',   Artist.objects.filter(drive_folder_url__gt='').order_by('name')),
            ('Promoter', PromoterProfile.objects.filter(drive_folder_url__gt='').order_by('name')),
        ]

        for label, qs in drive_sources:
            for obj in qs:
                url = obj.drive_folder_url
                if not DRIVE_FOLDER_RE.match(url):
                    drive_bad_count += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f'  BAD URL  {label}: {obj.name[:50]}  →  {url[:80]}'
                        )
                    )
                else:
                    self.stdout.write(f'  OK  {label}: {obj.name[:50]}')

        if not drive_bad_count:
            self.stdout.write(self.style.SUCCESS('  All Drive folder URLs look valid.'))

        # ── 3. PlaylistTrack records with missing stream_url ──────────────
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('PlaylistTrack — un-cached stream URLs'))

        uncached_qs = PlaylistTrack.objects.filter(
            drive_file_id__gt='', stream_url=''
        ).select_related('artist', 'promoter')

        for track in uncached_qs[:50]:
            uncached_count += 1
            owner = track.artist or track.promoter or '(no owner)'
            self.stdout.write(
                self.style.WARNING(
                    f'  NO STREAM_URL  {str(owner)[:30]} — {track.title[:50]}'
                    f'  [id: {track.drive_file_id}]'
                )
            )

        if uncached_qs.count() > 50:
            self.stdout.write(f'  … and {uncached_qs.count() - 50} more')

        if not uncached_count:
            self.stdout.write(self.style.SUCCESS('  All PlaylistTrack records have cached stream URLs.'))

        # ── Summary ────────────────────────────────────────────────────────
        self.stdout.write('')
        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(
            f'{prefix}Dangling photos: {dangling_count}  |  '
            f'Bad Drive URLs: {drive_bad_count}  |  '
            f'Un-cached tracks: {uncached_count}'
        )
