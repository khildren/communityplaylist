from datetime import timedelta

from django.shortcuts import render, get_object_or_404, redirect
from django.core.cache import cache
from django.utils import timezone

from django.http import JsonResponse

from .models import Topic, Reply, BannerMessage, Offering, PostReport
from .forms import TopicForm, ReplyForm, OfferingForm
from .spam import check_post, check_timing

# Rate limits
_RATE_LIMIT        = 5    # board posts per IP
_RATE_WINDOW       = 300  # 5 minutes
_GIVE_RATE_LIMIT   = 3    # offerings per IP
_GIVE_RATE_WINDOW  = 3600 # 1 hour


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


def _give_rate_limited(request):
    ip = _get_ip(request)
    key = f'give_post_{ip}'
    count = cache.get(key, 0)
    if count >= _GIVE_RATE_LIMIT:
        return True
    cache.set(key, count + 1, _GIVE_RATE_WINDOW)
    return False


# ── Board views ────────────────────────────────────────────────────────────────

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
    spam_err = None
    if request.method == 'POST':
        if _rate_limited(request):
            rate_err = True
        else:
            form = TopicForm(request.POST)
            if form.is_valid():
                # Timing honeypot
                tok, terr = check_timing(form.cleaned_data.get('_t', ''))
                if not tok:
                    spam_err = terr
                else:
                    ok, err = check_post(
                        title=form.cleaned_data.get('title', ''),
                        body=form.cleaned_data.get('body', ''),
                        author_name=form.cleaned_data.get('author_name', ''),
                        user=request.user,
                    )
                    if not ok:
                        spam_err = err
                    else:
                        topic = form.save()
                        return redirect(topic.get_absolute_url())
    else:
        form = TopicForm()
    return render(request, 'board/board_new.html', {
        'form': form if not rate_err else TopicForm(),
        'banner': _banner(),
        'rate_err': rate_err,
        'spam_err': spam_err,
    })


def board_topic(request, pk, slug):
    topic = get_object_or_404(Topic, pk=pk)
    reply_form = ReplyForm()
    rate_err = False
    spam_err = None

    if request.method == 'POST':
        if _rate_limited(request):
            rate_err = True
        else:
            reply_form = ReplyForm(request.POST)
            if reply_form.is_valid():
                tok, terr = check_timing(reply_form.cleaned_data.get('_t', ''))
                if not tok:
                    spam_err = terr
                else:
                    ok, err = check_post(
                        body=reply_form.cleaned_data.get('body', ''),
                        author_name=reply_form.cleaned_data.get('author_name', ''),
                        user=request.user,
                    )
                    if not ok:
                        spam_err = err
                    else:
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
        'spam_err': spam_err,
    })


# ── Give / Free & Trade views ──────────────────────────────────────────────────

def give_list(request):
    from events.models import Neighborhood
    now = timezone.now()
    qs = Offering.objects.filter(active=True, is_claimed=False, expires_at__gt=now)

    hood_slug = request.GET.get('hood', '')
    category  = request.GET.get('cat', '')
    hood_obj  = None

    if hood_slug:
        hood_obj = Neighborhood.objects.filter(slug=hood_slug).first()
        if hood_obj:
            qs = qs.filter(neighborhood=hood_obj)

    if category:
        qs = qs.filter(category=category)

    neighborhoods = Neighborhood.objects.filter(
        offerings__active=True,
        offerings__is_claimed=False,
        offerings__expires_at__gt=now,
    ).distinct().order_by('name')

    return render(request, 'board/give_list.html', {
        'offerings': qs,
        'neighborhoods': neighborhoods,
        'hood_slug': hood_slug,
        'hood_obj': hood_obj,
        'category': category,
        'banner': _banner(),
        'offer_categories': Offering.CATEGORY_CHOICES,
    })


def give_new(request):
    from events.models import Neighborhood
    from django.utils.text import slugify
    from .geo import ip_near_portland, geocode_neighborhood

    ip = _get_ip(request)
    geo_err = rate_err = hood_geo_err = False
    spam_err = None

    hood_slug = request.GET.get('hood', '')
    hood_obj = Neighborhood.objects.filter(slug=hood_slug).first() if hood_slug else None

    if request.method == 'POST':
        if _give_rate_limited(request):
            rate_err = True
        elif not ip_near_portland(ip):
            geo_err = True
        else:
            form = OfferingForm(request.POST, request.FILES)
            if form.is_valid():
                tok, terr = check_timing(form.cleaned_data.get('_t', ''))
                if not tok:
                    spam_err = terr
                else:
                    ok, err = check_post(
                        title=form.cleaned_data.get('title', ''),
                        body=form.cleaned_data.get('body', ''),
                        author_name=form.cleaned_data.get('author_name', ''),
                        user=request.user,
                    )
                    if not ok:
                        spam_err = err
                    else:
                        offering = form.save(commit=False)

                        # Resolve neighborhood — auto-create if "not listed"
                        new_hood_name = form.cleaned_data.get('new_neighborhood_name', '').strip()
                        if new_hood_name and not offering.neighborhood:
                            hood_slug_new = slugify(new_hood_name)
                            existing = Neighborhood.objects.filter(slug=hood_slug_new).first()
                            if existing:
                                offering.neighborhood = existing
                            else:
                                lat, lon = geocode_neighborhood(new_hood_name)
                                if lat is None:
                                    hood_geo_err = True
                                else:
                                    new_hood = Neighborhood.objects.create(
                                        name=new_hood_name.title(),
                                        slug=hood_slug_new,
                                        latitude=lat,
                                        longitude=lon,
                                        active=True,
                                    )
                                    offering.neighborhood = new_hood

                        if hood_geo_err:
                            return render(request, 'board/give_new.html', {
                                'form': form, 'banner': _banner(),
                                'hood_geo_err': hood_geo_err, 'new_hood_name': new_hood_name,
                            })

                        offering.poster_ip = ip
                        offering.expires_at = timezone.now() + timedelta(days=30)
                        if request.user.is_authenticated:
                            offering.poster_user = request.user
                        offering.save()

                        # Auto-create linked board thread
                        cat_label = dict(Offering.CATEGORY_CHOICES).get(offering.category, 'Offering')
                        contact_line = offering.contact_hint or 'reply to this thread'
                        thread_body = (offering.body + f'\n\n— Contact: {contact_line}').strip()
                        topic = Topic.objects.create(
                            title=f'[{cat_label}] {offering.title}',
                            body=thread_body,
                            author_name=offering.author_name,
                            category='offer',
                            neighborhood=offering.neighborhood,
                        )
                        offering.board_topic = topic
                        offering.save(update_fields=['board_topic'])
                        return redirect(offering.get_absolute_url())
    else:
        initial = {'neighborhood': hood_obj} if hood_obj else {}
        form = OfferingForm(initial=initial)

    return render(request, 'board/give_new.html', {
        'form': form if not rate_err else OfferingForm(),
        'banner': _banner(),
        'rate_err': rate_err,
        'geo_err': geo_err,
        'hood_geo_err': hood_geo_err,
        'spam_err': spam_err,
        'user_handle': request.user.profile.handle if request.user.is_authenticated and hasattr(request.user, 'profile') else None,
    })


def give_detail(request, pk, slug):
    offering = get_object_or_404(Offering, pk=pk, active=True)
    thread_replies = offering.board_topic.replies.all() if offering.board_topic else []

    # Message bridge: pass poster's profile if viewer and poster are both logged in
    poster_profile = None
    if (request.user.is_authenticated and offering.poster_user
            and request.user != offering.poster_user):
        try:
            poster_profile = offering.poster_user.profile
        except Exception:
            pass

    return render(request, 'board/give_detail.html', {
        'offering': offering,
        'thread_replies': thread_replies,
        'poster_profile': poster_profile,
        'banner': _banner(),
    })


def report_post(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    import json as _json
    try:
        body = _json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    target_type = body.get('target_type', '')
    target_id   = body.get('target_id')
    reason      = body.get('reason', '').strip()
    note        = body.get('note', '').strip()[:500]

    valid_types   = {t for t, _ in PostReport.TARGET_CHOICES}
    valid_reasons = {r for r, _ in PostReport.REASON_CHOICES}

    if target_type not in valid_types:
        return JsonResponse({'error': 'invalid target_type'}, status=400)
    if reason not in valid_reasons:
        return JsonResponse({'error': 'Please select a reason.'}, status=400)
    try:
        target_id = int(target_id)
    except (TypeError, ValueError):
        return JsonResponse({'error': 'invalid target_id'}, status=400)

    ip = _get_ip(request)

    # De-dupe: one report per IP per target per hour
    from django.utils import timezone as tz
    recent = PostReport.objects.filter(
        target_type=target_type,
        target_id=target_id,
        reporter_ip=ip,
        created_at__gte=tz.now() - timezone.timedelta(hours=1),
    ).exists()
    if recent:
        return JsonResponse({'ok': True})  # silently succeed — don't reveal de-dupe

    PostReport.objects.create(
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        note=note,
        reporter_ip=ip,
        reporter_user=request.user if request.user.is_authenticated else None,
    )
    return JsonResponse({'ok': True})


def give_claim(request, pk):
    if request.method != 'POST':
        return redirect('give_list')
    offering = get_object_or_404(Offering, pk=pk, active=True)
    if not offering.is_claimed:
        offering.is_claimed = True
        offering.claimed_at = timezone.now()
        offering.save(update_fields=['is_claimed', 'claimed_at'])
        if offering.board_topic:
            Reply.objects.create(
                topic=offering.board_topic,
                body='✓ This item has been claimed.',
                author_name='Community Playlist',
            )
    return redirect(offering.get_absolute_url())
