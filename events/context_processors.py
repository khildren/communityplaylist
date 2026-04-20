def featured_record(request):
    """Inject a random available record listing for the marquee / global promo."""
    try:
        from events.models import RecordListing
        record = RecordListing.objects.filter(is_available=True).order_by('?').first()
        return {'featured_record': record}
    except Exception:
        return {}


def admin_pending(request):
    """Inject pending-item counts into every admin page for the notification bar."""
    if not request.path.startswith('/admin/') or not getattr(request.user, 'is_staff', False):
        return {}
    try:
        from events.models import Event, Venue, EditSuggestion
        events_p  = Event.objects.filter(status='pending').count()
        venues_p  = Venue.objects.filter(verified=False, claimed_by__isnull=False).count()
        suggest_p = EditSuggestion.objects.filter(status='pending').count()
        return {
            'pending_events':      events_p,
            'pending_venues':      venues_p,
            'pending_suggestions': suggest_p,
            'pending_total':       events_p + venues_p + suggest_p,
        }
    except Exception:
        return {}
