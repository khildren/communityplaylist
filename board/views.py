from django.shortcuts import render, get_object_or_404, redirect
from django.core.cache import cache
from .models import Topic, Reply, BannerMessage
from .forms import TopicForm, ReplyForm

# Rate limit: max posts per IP within this window
_RATE_LIMIT   = 5    # max submissions
_RATE_WINDOW  = 300  # seconds (5 minutes)


def _banner():
    return BannerMessage.objects.filter(active=True).first()


def _get_ip(request):
    return (
        request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
        or request.META.get('REMOTE_ADDR', '')
    )


def _rate_limited(request):
    ip = _get_ip(request)
    key = f'board_post_{ip}'
    count = cache.get(key, 0)
    if count >= _RATE_LIMIT:
        return True
    cache.set(key, count + 1, _RATE_WINDOW)
    return False


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
    rate_err = False
    if request.method == 'POST':
        if _rate_limited(request):
            rate_err = True
        else:
            form = TopicForm(request.POST)
            if form.is_valid():
                topic = form.save()
                return redirect(topic.get_absolute_url())
    else:
        form = TopicForm()
    return render(request, 'board/board_new.html', {
        'form': form if not rate_err else TopicForm(),
        'banner': _banner(),
        'rate_err': rate_err,
    })


def board_topic(request, pk, slug):
    topic = get_object_or_404(Topic, pk=pk)
    reply_form = ReplyForm()
    rate_err = False

    if request.method == 'POST':
        if _rate_limited(request):
            rate_err = True
        else:
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
        'rate_err': rate_err,
    })
