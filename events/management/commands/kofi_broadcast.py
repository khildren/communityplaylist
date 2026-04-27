"""
management command: kofi_broadcast

Posts a daily Ko-fi support request to Discord and Bluesky.
Run once daily via cron, e.g.:

    0 10 * * * docker exec cp-communityplaylist-1 python manage.py kofi_broadcast

Use --dry-run to preview output without posting.
"""
from django.core.management.base import BaseCommand
from events.kofi import kofi_daily_broadcast


class Command(BaseCommand):
    help = 'Post daily Ko-fi awareness message to Discord + Bluesky'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        discord_ok, bluesky_ok = kofi_daily_broadcast(dry_run=dry_run)
        prefix = '[DRY] ' if dry_run else ''
        self.stdout.write(
            f'{prefix}Ko-fi broadcast — Discord: {"ok" if discord_ok else "skip"}, '
            f'Bluesky: {"ok" if bluesky_ok else "skip"}'
        )
