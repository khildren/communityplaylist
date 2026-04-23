"""
management command: flush_social_queue

Processes pending SocialQueue entries — posts board topics and Free & Trade
offerings to Bluesky and Discord after their post_after delay has passed.

Respects the SOCIAL_DAILY_POST_LIMIT (default 27) by counting how many
posts have already gone out today before processing the queue.

Run via cron every 15 minutes:
    */15 * * * * docker exec cp-local-cp-local-1 python manage.py flush_social_queue
"""
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Flush pending social media post queue'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Print what would be posted without actually posting')

    def handle(self, *args, **options):
        from django.conf import settings
        from board.models import SocialQueue, Topic, Offering
        from board.social import post_topic, post_offering
        from board.spam import check_post

        dry_run   = options['dry_run']
        limit     = getattr(settings, 'SOCIAL_DAILY_POST_LIMIT', 27)
        now       = timezone.now()
        today     = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # How many posts have gone out today already?
        posted_today = SocialQueue.objects.filter(
            status='posted', posted_at__gte=today
        ).count()
        remaining = max(0, limit - posted_today)

        if remaining == 0:
            self.stdout.write(f'[social] daily limit reached ({limit}), skipping.')
            return

        pending = SocialQueue.objects.filter(
            status='queued', post_after__lte=now
        ).order_by('post_after')[:remaining]

        if not pending.exists():
            self.stdout.write('[social] nothing to post.')
            return

        posted = 0
        for item in pending:
            try:
                if item.target_type == 'topic':
                    try:
                        topic = Topic.objects.get(pk=item.target_id)
                    except Topic.DoesNotExist:
                        item.status = 'skipped'
                        item.error = 'topic not found'
                        item.save(update_fields=['status', 'error'])
                        continue

                    # Re-run spam check — post might have been flagged since queuing
                    ok, err = check_post(
                        title=topic.title,
                        body=topic.body,
                        author_name=topic.author_name,
                    )
                    if not ok:
                        item.status = 'skipped'
                        item.error = f'spam: {err}'
                        item.save(update_fields=['status', 'error'])
                        self.stdout.write(f'  skip topic #{topic.pk} (spam: {err})')
                        continue

                    # Check for reports on this topic
                    from board.models import PostReport
                    if PostReport.objects.filter(
                        target_type='topic', target_id=topic.pk, resolved=False
                    ).exists():
                        item.status = 'skipped'
                        item.error = 'unresolved report'
                        item.save(update_fields=['status', 'error'])
                        self.stdout.write(f'  skip topic #{topic.pk} (unresolved report)')
                        continue

                    if dry_run:
                        self.stdout.write(f'  [DRY] would post topic #{topic.pk}: {topic.title}')
                    else:
                        bsky_ok, disc_ok = post_topic(topic)
                        item.status    = 'posted'
                        item.posted_at = timezone.now()
                        item.save(update_fields=['status', 'posted_at'])
                        self.stdout.write(f'  ✓ topic #{topic.pk} — bsky:{bsky_ok} disc:{disc_ok}')
                        posted += 1

                elif item.target_type == 'offering':
                    try:
                        offering = Offering.objects.select_related('neighborhood').get(pk=item.target_id)
                    except Offering.DoesNotExist:
                        item.status = 'skipped'
                        item.error = 'offering not found'
                        item.save(update_fields=['status', 'error'])
                        continue

                    if not offering.active or offering.is_claimed:
                        item.status = 'skipped'
                        item.error = 'inactive or already claimed'
                        item.save(update_fields=['status', 'error'])
                        continue

                    # Check for reports
                    from board.models import PostReport
                    if PostReport.objects.filter(
                        target_type='offering', target_id=offering.pk, resolved=False
                    ).exists():
                        item.status = 'skipped'
                        item.error = 'unresolved report'
                        item.save(update_fields=['status', 'error'])
                        continue

                    if dry_run:
                        self.stdout.write(f'  [DRY] would post offering #{offering.pk}: {offering.title}')
                    else:
                        bsky_ok, disc_ok = post_offering(offering)
                        item.status    = 'posted'
                        item.posted_at = timezone.now()
                        item.save(update_fields=['status', 'posted_at'])
                        self.stdout.write(f'  ✓ offering #{offering.pk} — bsky:{bsky_ok} disc:{disc_ok}')
                        posted += 1

            except Exception as e:
                item.status = 'failed'
                item.error  = str(e)[:500]
                item.save(update_fields=['status', 'error'])
                self.stdout.write(f'  ✗ {item.target_type}#{item.target_id} failed: {e}')

        self.stdout.write(f'[social] done — {posted} posted, {limit - posted_today - posted} remaining today.')
