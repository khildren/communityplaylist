from django.contrib import admin
from django.utils import html as admin_html
from .models import BannerMessage, Topic, Reply, Offering, PostReport, SocialQueue


class ReplyInline(admin.TabularInline):
    model = Reply
    extra = 0
    fields = ['author_name', 'body', 'created_at']
    readonly_fields = ['created_at']


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display  = ['title', 'category', 'author_name', 'pinned', 'reply_count', 'created_at']
    list_editable = ['pinned']
    list_filter   = ['category', 'pinned']
    search_fields = ['title', 'body', 'author_name']
    inlines       = [ReplyInline]

    def reply_count(self, obj):
        return obj.replies.count()
    reply_count.short_description = 'Replies'


@admin.register(Reply)
class ReplyAdmin(admin.ModelAdmin):
    list_display  = ['author_name', 'topic', 'created_at']
    search_fields = ['body', 'author_name']
    raw_id_fields = ['topic']


@admin.register(BannerMessage)
class BannerMessageAdmin(admin.ModelAdmin):
    list_display  = ['text', 'active', 'created_at']
    list_editable = ['active']


@admin.register(SocialQueue)
class SocialQueueAdmin(admin.ModelAdmin):
    list_display  = ['target_type', 'target_id', 'status', 'post_after', 'posted_at', 'error']
    list_filter   = ['target_type', 'status']
    readonly_fields = ['target_type', 'target_id', 'created_at', 'posted_at', 'bluesky_uri', 'error']
    actions = ['requeue_failed']

    def requeue_failed(self, request, queryset):
        from django.utils import timezone
        from datetime import timedelta
        queryset.filter(status='failed').update(
            status='queued',
            post_after=timezone.now() + timedelta(minutes=15),
            error='',
        )
    requeue_failed.short_description = 'Re-queue failed items (15 min)'


@admin.register(PostReport)
class PostReportAdmin(admin.ModelAdmin):
    list_display  = ['target_type', 'target_id', 'reason', 'reporter_ip', 'resolved', 'created_at', 'view_link']
    list_editable = ['resolved']
    list_filter   = ['target_type', 'reason', 'resolved']
    search_fields = ['note', 'reporter_ip']
    readonly_fields = ['target_type', 'target_id', 'reason', 'note', 'reporter_ip', 'reporter_user', 'created_at']
    actions = ['mark_resolved']

    def view_link(self, obj):
        url = obj.get_target_url()
        if url:
            return admin_html.format_html('<a href="{}" target="_blank">View →</a>', url)
        return '—'
    view_link.short_description = 'Post'

    def mark_resolved(self, request, queryset):
        queryset.update(resolved=True)
    mark_resolved.short_description = 'Mark selected as resolved'


@admin.register(Offering)
class OfferingAdmin(admin.ModelAdmin):
    list_display  = ['title', 'category', 'author_name', 'neighborhood', 'is_claimed', 'active', 'expires_at', 'created_at']
    list_editable = ['is_claimed', 'active']
    list_filter   = ['category', 'is_claimed', 'active', 'neighborhood']
    search_fields = ['title', 'body', 'author_name', 'poster_ip']
    raw_id_fields = ['board_topic', 'neighborhood']
    readonly_fields = ['poster_ip', 'created_at', 'claimed_at']
