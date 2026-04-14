"""
Compress uploaded images to reduce disk usage.

Usage:
  python manage.py compress_images
  python manage.py compress_images --quality 82 --max-width 1920
  python manage.py compress_images --dry-run
"""
import os
import glob

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Compress uploaded images (JPEG progressive, PNG optimize, resize oversized)'

    def add_arguments(self, parser):
        parser.add_argument('--quality',   type=int, default=82,   help='JPEG quality 1–95 (default 82)')
        parser.add_argument('--max-width', type=int, default=1920, help='Max width in px (default 1920)')
        parser.add_argument('--min-size',  type=int, default=40,   help='Skip files smaller than N KB (default 40)')
        parser.add_argument('--dry-run',   action='store_true',    help='Report savings without writing files')

    def handle(self, *args, **options):
        try:
            from PIL import Image
        except ImportError:
            self.stderr.write('Pillow not installed. Run: pip install Pillow')
            return

        quality   = options['quality']
        max_width = options['max_width']
        min_bytes = options['min_size'] * 1024
        dry_run   = options['dry_run']
        media_root = str(settings.MEDIA_ROOT)

        self.stdout.write(f'Scanning {media_root} …')
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no files will be modified'))

        exts = ('*.jpg', '*.jpeg', '*.png', '*.JPG', '*.JPEG', '*.PNG')
        files = []
        for pat in exts:
            files.extend(glob.glob(os.path.join(media_root, '**', pat), recursive=True))

        self.stdout.write(f'Found {len(files)} image files')

        saved = 0
        processed = 0
        skipped = 0
        errors = 0

        for fpath in files:
            orig_size = os.path.getsize(fpath)
            if orig_size < min_bytes:
                skipped += 1
                continue

            try:
                img = Image.open(fpath)
                orig_fmt = (img.format or 'JPEG').upper()

                # Downscale if wider than max_width
                resized = False
                if img.width > max_width:
                    ratio = max_width / img.width
                    img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
                    resized = True

                if not dry_run:
                    if orig_fmt == 'JPEG':
                        if img.mode not in ('RGB', 'L'):
                            img = img.convert('RGB')
                        img.save(fpath, 'JPEG', quality=quality, optimize=True, progressive=True)
                    elif orig_fmt == 'PNG':
                        img.save(fpath, 'PNG', optimize=True)
                    else:
                        img.save(fpath, optimize=True, quality=quality)
                    new_size = os.path.getsize(fpath)
                    delta = orig_size - new_size
                else:
                    # Rough estimate for dry run
                    delta = int(orig_size * 0.25) if orig_fmt == 'JPEG' else int(orig_size * 0.1)
                    new_size = orig_size - delta

                saved += max(0, delta)
                processed += 1

                if delta > 5000 or resized:
                    pct = int(delta / orig_size * 100) if orig_size else 0
                    note = f' [resized to {max_width}px]' if resized else ''
                    self.stdout.write(
                        f'  {os.path.relpath(fpath, media_root)}: '
                        f'{self._fmt(orig_size)} → {self._fmt(new_size)} (-{pct}%){note}'
                    )

            except Exception as exc:
                self.stderr.write(f'  ERROR {fpath}: {exc}')
                errors += 1

        def fmt(b):
            if b >= 1_000_000: return f'{b/1_000_000:.1f} MB'
            if b >= 1_000:     return f'{b/1_000:.1f} KB'
            return f'{b} B'

        action = 'Would save' if dry_run else 'Saved'
        self.stdout.write(self.style.SUCCESS(
            f'\nDone — processed {processed}, skipped {skipped} (< {options["min_size"]} KB), '
            f'errors {errors}\n{action}: {fmt(saved)}'
        ))

    @staticmethod
    def _fmt(b):
        if b >= 1_000_000: return f'{b/1_000_000:.1f} MB'
        if b >= 1_000:     return f'{b/1_000:.1f} KB'
        return f'{b} B'
