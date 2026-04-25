"""
db_health — System health snapshot: row counts, task backlog, and pending review age.

Reports:
  - Event counts by status (pending / approved / rejected / cancelled)
  - Events that have been pending review longer than --pending-days (default 3)
  - WorkerTask queue: queued, running (with stuck detection), error, done
  - VenueFeed: active vs inactive
  - Artist / Promoter / Venue / PlaylistTrack totals

Posts a Discord alert to DISCORD_WEBHOOK_OPS when any threshold is breached,
or unconditionally with --notify.

Usage:
  python manage.py db_health
  python manage.py db_health --pending-days 5
  python manage.py db_health --notify            # always post to Discord
  python manage.py db_health --quiet             # only print/ping on issues
"""
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone
from datetime import timedelta

from events.models import (
    Artist, Event, PlaylistTrack, PromoterProfile, VenueFeed, Venue, WorkerTask,
)
from events.utils.url_safety import discord_send as _discord_send

WEBHOOK = getattr(settings, 'DISCORD_WEBHOOK_OPS', '')

STUCK_RUNNING_MINUTES = 30   # running tasks older than this are flagged stuck


class Command(BaseCommand):
    help = 'System health snapshot: row counts, task queue status, pending review age'

    def add_arguments(self, parser):
        parser.add_argument('--pending-days', type=int, default=3,
                            help='Flag events pending review longer than N days (default 3)')
        parser.add_argument('--notify', action='store_true',
                            help='Always post summary to DISCORD_WEBHOOK_OPS (not just on issues)')
        parser.add_argument('--quiet', action='store_true',
                            help='Suppress console output; only print when issues found')

    def handle(self, *args, **options):
        now           = timezone.now()
        pending_days  = options['pending_days']
        always_notify = options['notify']
        quiet         = options['quiet']

        alerts = []
        lines  = []

        def out(msg, style=None):
            if not quiet:
                self.stdout.write(style(msg) if style else msg)
            lines.append(msg)

        def alert(msg, style=None):
            """Flag a threshold breach — always printed, always Discord-pinged."""
            self.stdout.write((style or self.style.ERROR)(f'  ⚠  {msg}'))
            alerts.append(msg)

        # ── Events ────────────────────────────────────────────────────────────
        out(self.style.MIGRATE_HEADING('Events'), style=None)

        status_counts = dict(
            Event.objects.values_list('status').annotate(n=Count('pk')).values_list('status', 'n')
        )
        pending  = status_counts.get('pending', 0)
        approved = status_counts.get('approved', 0)
        rejected = status_counts.get('rejected', 0)
        total_ev = sum(status_counts.values())

        out(f'  Total: {total_ev}  |  Approved: {approved}  |  Pending: {pending}  |  Rejected: {rejected}')

        # Pending-too-long
        cutoff = now - timedelta(days=pending_days)
        stale_pending = Event.objects.filter(status='pending', created_at__lt=cutoff)
        stale_count   = stale_pending.count()
        if stale_count:
            alert(f'{stale_count} events pending review > {pending_days} days:')
            for ev in stale_pending.order_by('created_at')[:10]:
                age = (now - ev.created_at).days
                out(f'    [{age}d]  {ev.title[:60]}  (submitted: {ev.submitted_by or "anon"})')
            if stale_count > 10:
                out(f'    … and {stale_count - 10} more')
        else:
            out(f'  Pending review queue clean (none older than {pending_days}d)')

        # ── WorkerTask ────────────────────────────────────────────────────────
        out('')
        out(self.style.MIGRATE_HEADING('WorkerTask Queue'), style=None)

        task_counts = dict(
            WorkerTask.objects.values_list('status').annotate(n=Count('pk')).values_list('status', 'n')
        )
        queued  = task_counts.get('queued',  0)
        running = task_counts.get('running', 0)
        error   = task_counts.get('error',   0)
        done    = task_counts.get('done',    0)

        out(f'  Queued: {queued}  |  Running: {running}  |  Error: {error}  |  Done: {done}')

        if queued > 50:
            alert(f'WorkerTask backlog: {queued} tasks queued — worker may be stuck')

        if error > 10:
            alert(f'WorkerTask errors: {error} failed tasks')
            err_sample = WorkerTask.objects.filter(status='error').order_by('-created_at')[:5]
            for t in err_sample:
                out(f'    #{t.pk}  {t.task_type}  {t.error_msg[:80]}')

        # Stuck running tasks
        stuck_cutoff = now - timedelta(minutes=STUCK_RUNNING_MINUTES)
        stuck = WorkerTask.objects.filter(status='running', created_at__lt=stuck_cutoff)
        if stuck.exists():
            alert(f'{stuck.count()} task(s) stuck in "running" state > {STUCK_RUNNING_MINUTES} min')
            for t in stuck[:5]:
                age_min = int((now - t.created_at).total_seconds() / 60)
                out(f'    #{t.pk}  {t.task_type}  ({age_min} min)')

        # ── Feeds ─────────────────────────────────────────────────────────────
        out('')
        out(self.style.MIGRATE_HEADING('VenueFeed'), style=None)
        active_feeds   = VenueFeed.objects.filter(active=True).count()
        inactive_feeds = VenueFeed.objects.filter(active=False).count()
        erroring_feeds = VenueFeed.objects.filter(active=True, last_error__gt='').count()
        out(f'  Active: {active_feeds}  |  Inactive: {inactive_feeds}  |  Erroring: {erroring_feeds}')
        if erroring_feeds > 3:
            alert(f'{erroring_feeds} active feeds have errors — run check_stale_feeds')

        # ── Profiles ──────────────────────────────────────────────────────────
        out('')
        out(self.style.MIGRATE_HEADING('Profile Counts'), style=None)
        artists    = Artist.objects.count()
        stubs      = Artist.objects.filter(is_stub=True).count()
        promoters  = PromoterProfile.objects.filter(is_public=True).count()
        venues     = Venue.objects.filter(active=True).count()
        tracks     = PlaylistTrack.objects.count()
        out(
            f'  Artists: {artists} ({stubs} stubs)  |  Promoters: {promoters}  '
            f'|  Venues: {venues}  |  Tracks: {tracks}'
        )

        # ── Discord ───────────────────────────────────────────────────────────
        if alerts or always_notify:
            icon    = '🔴' if alerts else '✅'
            header  = f'{icon} **CommunityPlaylist DB Health** — {now:%Y-%m-%d %H:%M}'
            body    = '\n'.join(f'• {a}' for a in alerts) if alerts else 'All systems nominal.'
            counts  = (
                f'Events: {total_ev} | Pending: {pending} | '
                f'Queue: {queued}q {running}r {error}e | '
                f'Feeds: {active_feeds} active'
            )
            _discord_send(WEBHOOK, {'content': f'{header}\n{body}\n_{counts}_'})

        if not alerts:
            self.stdout.write(self.style.SUCCESS('  No threshold breaches detected.'))
