from django.shortcuts import render, get_object_or_404, redirect
from .models import Topic, Reply, BannerMessage
from .forms import TopicForm, ReplyForm


def _banner():
    return BannerMessage.objects.filter(active=True).first()


def board_list(request):
    topics = Topic.objects.all().prefetch_related('replies')
    return render(request, 'board/board_list.html', {
        'topics': topics,
        'banner': _banner(),
    })


def board_aid(request):
    topics = Topic.objects.filter(category='aid').prefetch_related('replies')
    return render(request, 'board/board_list.html', {
        'topics': topics,
        'banner': _banner(),
        'aid_filter': True,
    })


def board_new(request):
    if request.method == 'POST':
        form = TopicForm(request.POST)
        if form.is_valid():
            topic = form.save()
            return redirect(topic.get_absolute_url())
    else:
        form = TopicForm()
    return render(request, 'board/board_new.html', {
        'form': form,
        'banner': _banner(),
    })


def board_topic(request, pk, slug):
    topic = get_object_or_404(Topic, pk=pk)
    reply_form = ReplyForm()

    if request.method == 'POST':
        reply_form = ReplyForm(request.POST)
        if reply_form.is_valid():
            reply = reply_form.save(commit=False)
            reply.topic = topic
            reply.save()
            return redirect(topic.get_absolute_url())

    return render(request, 'board/board_topic.html', {
        'topic': topic,
        'replies': topic.replies.all(),
        'reply_form': reply_form,
        'banner': _banner(),
    })
