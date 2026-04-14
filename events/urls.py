from django.urls import path
from . import views

urlpatterns = [
    path('', views.event_list, name='event_list'),
    path('submit/', views.event_submit, name='event_submit'),
    path('archive/', views.event_archive, name='event_archive'),
    path('genres/', views.genre_autocomplete, name='genre_autocomplete'),
    path('artists/', views.artist_autocomplete, name='artist_autocomplete'),
    path('artists/add/', views.artist_add, name='artist_add'),
    path('artists/<int:pk>/', views.artist_profile, name='artist_profile'),
    path('events/<slug:slug>/', views.event_detail, name='event_detail'),
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
    path('feed/events.ics', views.calendar_feed, name='calendar_feed'),
    path('subscribe/', views.calendar_subscribe, name='calendar_subscribe'),
    path('features/', views.features_page, name='features'),
    # Auth
    path('register/', views.register_view, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
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
