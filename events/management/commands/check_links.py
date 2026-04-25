"""
check_links — Weekly URL health sweep.

Checks website fields on Artist, PromoterProfile, and Venue.
Sets link_broken=True and updates link_checked_at on each object.
Broken links show in admin via the link_broken filter (orphan bucket).

Usage:
  python manage.py check_links           # all models
  python manage.py check_links --dry-run # print results, no DB writes
"""
import time
import logging

import requests
from django.core.management.base import BaseCommand
from django.utils import timezone

from events.models import Artist, PromoterProfile, Venue
from events.utils.url_safety import is_safe_url

logger = logging.getLogger(__name__)

TIMEOUT   = 10
HEADERS   = {'User-Agent': 'CommunityPlaylist-LinkChecker/1.0'}
SOURCES   = [
    ('Artist',   Artist.objects.filter(website__gt='')),
    ('Promoter', PromoterProfile.objects.filter(website__gt='', is_public=True)),
    ('Venue',    Venue.objects.filter(website__gt='', active=True)),
]


def check_url(url: str) -> tuple[bool, int]:
    """HEAD then GET fallback. Returns (is_ok, status_code)."""
    if not is_safe_url(url):
        return False, 0
    for method in ('HEAD', 'GET'):
        try:
            r = requests.request(
                method, url, headers=HEADERS,
                timeout=TIMEOUT, allow_redirects=True,
            )
            return r.status_code < 400, r.status_code
        except requests.exceptions.SSLError:
            return False, 0
        except requests.exceptions.ConnectionError:
            return False, 0
        except requests.exceptions.Timeout:
            return False, 0
        except Exception:
            return False, 0
    return False, 0


class Command(BaseCommand):
    help = 'Check website URLs for Artist, PromoterProfile, and Venue; flag broken ones'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Print results without updating the database')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        now     = timezone.now()

        total = broken = fixed = 0

        for label, qs in SOURCES:
            for obj in qs:
                total += 1
                ok, code = check_url(obj.website)
                was_broken = obj.link_broken

                if not dry_run:
                    obj.__class__.objects.filter(pk=obj.pk).update(
                        link_broken=not ok,
                        link_checked_at=now,
                    )

                if not ok:
                    broken += 1
                    tag = 'NEW' if not was_broken else 'still'
                    self.stdout.write(
                        self.style.WARNING(
                            f'  BROKEN [{tag}] {label}: {obj.name}  ({code or "timeout"})  {obj.website}'
                        )
                    )
                elif was_broken:
                    fixed += 1
                    self.stdout.write(
                        self.style.SUCCESS(f'  FIXED  {label}: {obj.name}  {obj.website}')
                    )

                time.sleep(0.5)  # polite crawl rate

        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write('')
        self.stdout.write(
            f'{prefix}Checked {total} URLs — '
            f'{broken} broken, {fixed} newly fixed, {total - broken - fixed} OK'
        )
