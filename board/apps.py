from django.apps import AppConfig


class BoardConfig(AppConfig):
    name = 'board'

    def ready(self):
        from django.db.models.signals import post_save
        from django.dispatch import receiver
        from django.utils import timezone
        from datetime import timedelta
        from .models import Topic, Offering

        @receiver(post_save, sender=Topic)
        def enqueue_topic(sender, instance, created, **kwargs):
            if not created:
                return
            # Skip auto-generated offer threads (linked to a Free & Trade Offering)
            if instance.category == 'offer':
                return
            from django.conf import settings
            delay = getattr(settings, 'SOCIAL_BOARD_DELAY_HOURS', 1)
            from .models import SocialQueue
            SocialQueue.objects.get_or_create(
                target_type='topic',
                target_id=instance.pk,
                defaults={'post_after': timezone.now() + timedelta(hours=delay)},
            )

        @receiver(post_save, sender=Offering)
        def enqueue_offering(sender, instance, created, **kwargs):
            if not created:
                return
            from django.conf import settings
            delay = getattr(settings, 'SOCIAL_BOARD_DELAY_HOURS', 1)
            from .models import SocialQueue
            SocialQueue.objects.get_or_create(
                target_type='offering',
                target_id=instance.pk,
                defaults={'post_after': timezone.now() + timedelta(hours=delay)},
            )
