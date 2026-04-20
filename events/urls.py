from django.urls import path
from . import views

urlpatterns = [
    # Music player API
    path('api/genres/filter/', views.api_genre_filter, name='api_genre_filter'),
    path('api/tracks/', views.playlist_tracks_json, name='playlist_tracks_json'),
    path('api/saved-tracks/', views.saved_tracks_json, name='saved_tracks_json'),
    path('api/videos/', views.api_video_queue, name='api_video_queue'),
    path('api/parse-lineup/', views.api_parse_lineup, name='api_parse_lineup'),
    path('api/events/<slug:slug>/', views.api_event_detail, name='api_event_detail'),
    path('api/shelters/', views.api_shelters, name='api_shelters'),
    path('save-track/', views.toggle_save_track, name='toggle_save_track'),
    path('drive-sync/', views.drive_sync, name='drive_sync'),
    path('tracks/<int:pk>/delete/', views.delete_track, name='delete_track'),
    # Promoters / Crews
    path('promoters/', views.promoter_list, name='promoter_list'),
    path('promoters/register/', views.promoter_register, name='promoter_register'),
    path('promoters/<slug:slug>/', views.promoter_detail, name='promoter_detail'),
    path('promoters/<slug:slug>/edit/', views.promoter_edit, name='promoter_edit'),
    path('promoters/<slug:slug>/sync-shop/', views.promoter_sync_shop, name='promoter_sync_shop'),
    path('promoters/<slug:slug>/reserve/<int:listing_pk>/', views.promoter_reserve, name='promoter_reserve'),
    path('promoters/<slug:slug>/reservations/', views.promoter_reservations, name='promoter_reservations'),
    path('', views.event_list, name='event_list'),
    path('submit/', views.event_submit, name='event_submit'),
    path('archive/', views.event_archive, name='event_archive'),
    path('genres/', views.genre_autocomplete, name='genre_autocomplete'),
    path('artists/', views.artist_autocomplete, name='artist_autocomplete'),
    path('artists/add/', views.artist_add, name='artist_add'),
    path('artists/register/', views.artist_register, name='artist_register'),
    path('artists/<int:pk>/', views.artist_by_pk, name='artist_by_pk'),       # legacy redirect
    path('artists/<slug:slug>/', views.artist_profile, name='artist_profile'),
    path('artists/<slug:slug>/edit/', views.artist_edit, name='artist_edit'),
    path('events/<slug:slug>/', views.event_detail, name='event_detail'),
    path('events/<slug:slug>/lineup/', views.event_lineup_edit, name='event_lineup_edit'),
    path('events/<slug:slug>/lineup/create/', views.event_lineup_create, name='event_lineup_create'),
    path('events/<slug:slug>/claim/', views.claim_event, name='claim_event'),
    path('events/<slug:slug>/edit/', views.event_edit, name='event_edit'),
    # Venues
    path('venues/', views.venue_list, name='venue_list'),
    path('venues/register/', views.venue_register, name='venue_register'),
    path('venues/<slug:slug>/', views.venue_detail, name='venue_detail'),
    path('venues/<slug:slug>/feed.ics', views.venue_feed, name='venue_feed'),
    path('venues/<slug:slug>/edit/', views.venue_edit, name='venue_edit'),
    # Neighborhoods
    path('neighborhoods/', views.neighborhood_list, name='neighborhood_list'),
    path('neighborhoods/<slug:slug>/', views.neighborhood_detail, name='neighborhood_detail'),
    # Calendar
    path('feed/events.ics',  views.calendar_feed, name='calendar_feed'),
    path('feed/events.rss',  views.events_rss,    name='events_rss'),
    path('shop/',            views.shop,           name='shop'),
    path('subscribe/', views.calendar_subscribe, name='calendar_subscribe'),
    path('features/', views.features_page, name='features'),
    # Auth
    path('register/', views.register_view, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('onboarding/', views.onboarding_view, name='onboarding'),
    path('dashboard/', views.dashboard, name='dashboard'),
    # User profiles
    path('verify-email/<str:token>/', views.verify_email, name='verify_email'),
    path('resend-verification/', views.resend_verification, name='resend_verification'),
    path('profile/settings/', views.profile_settings, name='profile_settings'),
    path('u/@<str:handle>/', views.public_profile, name='public_profile'),
    path('u/@<str:handle>/feed/', views.profile_feed, name='profile_feed'),
    path('follow/', views.toggle_follow, name='toggle_follow'),
    path('suggest-edit/', views.suggest_edit, name='suggest_edit'),
]
