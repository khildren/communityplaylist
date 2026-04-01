from django.contrib import admin
from .models import BannerMessage, Topic, Reply


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
